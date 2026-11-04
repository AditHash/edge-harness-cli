"""Rich renderables for the chat terminal: diffs, commands, tool calls and status lines.

All model- or repository-supplied text is rendered as plain text (no markup), so it cannot
inject styles or fake prompts.
"""

from typing import Any

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from code_harness.approvals import HOST_PREFIX
from code_harness.streaming import ThinkFilter, tool_summary

CODE_THEME = "monokai"
THEME = Theme(
    {
        "markdown.h1": "bold bright_cyan",
        "markdown.h2": "bold bright_blue",
        "markdown.h3": "bold bright_magenta",
        "markdown.h4": "bold magenta",
        "markdown.strong": "bold bright_white",
        "markdown.em": "italic bright_yellow",
        "markdown.code": "bright_green",
        "markdown.item.bullet": "bright_cyan",
        "markdown.item.number": "bright_cyan",
        "markdown.block_quote": "italic bright_magenta",
        "markdown.link": "bright_blue underline",
    }
)

DIFF_PREFIX = "Proposed change: "
COMMAND_PREFIX = "Proposed sandbox command"


def diff_stats(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def diff_panel(path: str, diff: str) -> Panel:
    added, removed = diff_stats(diff)
    title = Text.assemble(("edit ", "bold yellow"), (path, "bold"))
    subtitle = Text.assemble((f"+{added}", "green"), " ", (f"-{removed}", "red"))
    body = Syntax(diff.rstrip("\n"), "diff", theme="ansi_dark", word_wrap=True)
    return Panel(body, title=title, subtitle=subtitle, border_style="yellow", expand=True)


def command_panel(command: str) -> Panel:
    body = Group(
        Syntax(command, "bash", theme="ansi_dark", word_wrap=True),
        Text("copied workspace, no network, live files unchanged", style="dim"),
    )
    title = Text.assemble(("$ ", "bold cyan"), ("sandbox command", "bold"))
    return Panel(body, title=title, border_style="cyan", expand=True)


HISTORY_LIMIT = 20


def review_renderable(text: str) -> RenderableType:
    """Turn an approval display message into a panel; anything else stays plain text."""
    if text.startswith(DIFF_PREFIX):
        head, _, diff = text.partition("\n")
        return diff_panel(head.removeprefix(DIFF_PREFIX), diff)
    if text.startswith(COMMAND_PREFIX):
        lines = text.splitlines()
        if len(lines) >= 2:
            return command_panel("\n".join(lines[1:-1]) or lines[1])
    if text.startswith(HOST_PREFIX):
        return Panel(Text(text), title="REAL machine", border_style="bold red", expand=True)
    if text.startswith("MCP "):
        return Panel(Text(text), title="MCP", border_style="magenta", expand=True)
    return Text(text, style="dim" if text.startswith(("Applied under", "Run under")) else "")


_CHANGE_MARKS = {"created": ("+", "green"), "deleted": ("-", "red"), "edited": ("~", "yellow")}


def changes_table(changes: list[tuple[str, int, int, str]], title: str) -> Group:
    """Approved file changes with line counts; paths are shown literally."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(width=1)
    grid.add_column()
    grid.add_column(style="dim")
    grid.add_column(justify="right", style="green")
    grid.add_column(justify="right", style="red")
    for path, added, removed, kind in changes:
        symbol, color = _CHANGE_MARKS.get(kind, ("~", "yellow"))
        grid.add_row(Text(symbol, style=color), Text(path), kind, f"+{added}", f"-{removed}")
    return Group(Text(title, style="bold"), Padding(grid, (0, 0, 0, 2)))


# Plain names for built-in tools, with a color per kind of action. Anything else, such as an MCP
# tool, keeps its own name.
_TOOL_LOOK = {
    "list_repo": ("List files", "bright_cyan"),
    "read_code": ("Read", "bright_cyan"),
    "search_code": ("Search", "bright_cyan"),
    "find_relevant_code": ("Find", "bright_cyan"),
    "git_status": ("Git status", "bright_cyan"),
    "git_diff": ("Git diff", "bright_cyan"),
    "git_log": ("Git log", "bright_cyan"),
    "update_plan": ("Plan", "bright_blue"),
    "edit_code": ("Write", "bright_yellow"),
    "replace_in_file": ("Edit", "bright_yellow"),
    "delete_code": ("Delete", "bright_yellow"),
    "run_sandboxed": ("Sandbox", "bright_magenta"),
    "run_on_host": ("Real machine", "bold bright_red"),
    "web_fetch": ("Fetch", "bright_blue"),
    "spawn_subagent": ("Subagent", "bright_green"),
}


def tool_label(name: str) -> str:
    return _TOOL_LOOK.get(name, (name, ""))[0]


def tool_row(summary: str, encoding: str | None) -> Table:
    """`name target` from the stream as a readable row; the target is shown literally."""
    name, _, target = summary.partition(" ")
    label, color = _TOOL_LOOK.get(name, (name, "bright_cyan"))
    body = Text.assemble((label, f"bold {color}"), " ", (target, "bright_white"))
    return bullet_row(body, mark(encoding), color)


def help_table() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold bright_cyan", no_wrap=True)
    table.add_column(style="dim")
    for command, description in SLASH_COMMANDS:
        table.add_row(command, description)
    return table


def cost_table(tracker: Any) -> RenderableType:
    """Estimated session cost by model, with the rate and where it came from."""
    from code_harness.pricing import format_usd

    if not tracker.by_model:
        return Text("No model calls in this session yet.")
    table = Table(title="Estimated session cost", title_style="bold", show_edge=False)
    table.add_column("model", style="cyan")
    for column in ("in", "cached", "out"):
        table.add_column(column, justify="right")
    table.add_column("cost", justify="right", style="bold green")
    table.add_column("$ per 1M in / cached / out", justify="right", style="dim")
    table.add_column("price from", style="dim")
    for model, entry in sorted(tracker.by_model.items()):
        price = tracker.prices.get(model) if tracker.prices is not None else None
        rates = "unknown"
        if price is not None:
            cached = "-" if price.cached is None else f"{price.cached:g}"
            rates = f"{price.input:g} / {cached} / {price.output:g}"
        known = price is not None or entry["cost"] > 0
        table.add_row(
            Text(model),
            short_tokens(int(entry["input"])),
            short_tokens(int(entry["cached"])),
            short_tokens(int(entry["output"])),
            f"~{format_usd(entry['cost'])}" if known else "unknown",
            rates,
            price.source if price is not None else "-",
        )
    return table


def session_header(
    *,
    workspace: str,
    model: str,
    session_id: str,
    guidance: list[str],
    version: str = "",
    rates: str = "",
) -> Group:
    """Start-of-chat banner: folder, model, price, session and how approvals work."""
    title = Text.assemble((">_ ", "bold bright_cyan"), ("Edge Harness", "bold"))
    if version:
        title.append(f" v{version}", style="dim")
    details = Table.grid(padding=(0, 2))
    details.add_column(style="dim", no_wrap=True)
    details.add_column()
    details.add_row("folder", Text(workspace, style="green"))
    details.add_row("model", Text(model, style="cyan"))
    if rates:
        details.add_row("price", Text(rates, style="dim"))
    details.add_row("session", Text(session_id, style="dim"))
    if guidance:
        details.add_row("guidance", Text(", ".join(guidance), style="dim"))
    hints = Text(
        "Edits and sandbox commands: y once, a rest of session, n no. "
        "Real machine commands need the word yes.\n/help commands   /settings options   /cost spend",
        style="dim",
    )
    panel = Panel(
        Group(details, Text(""), hints),
        title=title,
        title_align="left",
        border_style="bright_black",
        padding=(0, 1),
    )
    return Group(panel, Text(""))


def short_tokens(count: int) -> str:
    """1234 -> 1.2K, 1500000 -> 1.5M."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def short_duration(seconds: float) -> str:
    whole = max(0, round(seconds))
    if whole < 60:
        return f"{whole}s"
    return f"{whole // 60}m {whole % 60}s"


def mark(encoding: str | None) -> str:
    """A round bullet where the console can encode it, otherwise an ASCII one."""
    return "●" if (encoding or "").lower().replace("-", "").startswith("utf") else "*"


def branch(encoding: str | None) -> str:
    return "└" if mark(encoding) == "●" else "L"


def _printable(text: str) -> str:
    return "".join(char for char in text if char in "\n\t" or char.isprintable())


def history_items(messages: Any, limit: int = HISTORY_LIMIT) -> tuple[list[tuple[str, str]], int]:
    """The saved conversation as (kind, text) rows, kind being user, assistant or tool.

    Returns the latest `limit` rows and how many earlier ones were left out. Tool results and
    reasoning are not replayed. All of this text is untrusted, so it is shown literally.
    """
    items: list[tuple[str, str]] = []
    for message in messages if isinstance(messages, list) else []:
        role = "user" if message.get("role") == "user" else "assistant"
        for block in message.get("content", []):
            if "text" in block:
                text = str(block["text"])
                if role == "assistant":
                    think = ThinkFilter()
                    parts = [*think.feed(text), *think.finish_message()]
                    text = "".join(part for kind, part in parts if kind == "text_delta")
                if text.strip():
                    items.append((role, _printable(text).strip()))
            elif "image" in block:
                items.append((role, "[image]"))
            elif "toolUse" in block:
                use = block["toolUse"]
                items.append(("tool", tool_summary(str(use.get("name")), use.get("input"))))
    return items[-limit:], max(0, len(items) - limit)


def estimated_context_tokens(messages: Any) -> int:
    """Rough size of a restored conversation (~4 characters per token), images left out."""
    if not isinstance(messages, list):
        return 0
    size = sum(
        len(str(block))
        for message in messages
        for block in message.get("content", [])
        if "image" not in block
    )
    return size // 4


def user_bar(text: str) -> Padding:
    """The user's message as a full-width shaded bar; text is shown literally."""
    return Padding(Text.assemble(("> ", "bold bright_green"), text), (0, 1), style="on grey23")


def bullet_row(body: RenderableType, bullet: str, style: str) -> Table:
    """A bullet in its own column so wrapped lines stay aligned under the body."""
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1)
    grid.add_column(ratio=1)
    grid.add_row(Text(bullet, style=style), body)
    return grid


def status_segments(
    *,
    model: str,
    folder: str,
    approvals: str,
    context_used: int | None,
    context_limit: int | None,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    cost: str = "",
    cost_style: str = "fg:ansibrightgreen",
    fancy: bool = False,
) -> list[tuple[str, str]]:
    """Bottom status bar as (style, text) pairs for prompt_toolkit."""
    separator = ("fg:ansibrightblack", " · ")
    parts: list[tuple[str, str]] = [
        ("fg:ansicyan bold", model),
        ("fg:ansigreen", folder),
        ("fg:ansimagenta", approvals),
    ]
    if context_limit:
        used = context_used or 0
        left = max(0, 100 - round(100 * used / context_limit))
        color = "ansigreen" if left > 40 else "ansiyellow" if left > 15 else "ansired"
        if fancy:
            filled = min(8, round(8 * used / context_limit))
            parts.append((f"fg:{color}", "\u25b0" * filled + "\u25b1" * (8 - filled)))
        parts.append((f"fg:{color}", f"Context {left}% left"))
        parts.append(("fg:ansiwhite", f"{short_tokens(used)} / {short_tokens(context_limit)}"))
    parts.append(("fg:ansiblue", f"{short_tokens(input_tokens)} in"))
    if cached_tokens:
        parts.append(("fg:ansimagenta", f"{short_tokens(cached_tokens)} cached"))
    parts.append(("fg:ansigreen", f"{short_tokens(output_tokens)} out"))
    if cost:
        parts.append((cost_style, cost))
    parts.append(("fg:ansibrightblack", "/help"))
    segments: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        if index:
            segments.append(separator)
        segments.append(part)
    return segments


def prompt_style() -> Any:
    from prompt_toolkit.styles import Style

    return Style.from_dict(
        {
            "bottom-toolbar": "noreverse bg:default",
            "bottom-toolbar.text": "noreverse",
            "placeholder": "fg:ansibrightblack",
            "completion-menu.completion": "bg:ansibrightblack fg:ansiwhite",
            "completion-menu.completion.current": "bg:ansicyan fg:ansiblack bold",
            "completion-menu.meta.completion": "bg:ansibrightblack fg:ansiwhite",
            "completion-menu.meta.completion.current": "bg:ansicyan fg:ansiblack",
        }
    )


def _session_cost(info: Any) -> str:
    from code_harness.pricing import format_usd

    usage = getattr(info, "usage", None)
    spent = usage.get("spent") if isinstance(usage, dict) else None
    if isinstance(spent, bool) or not isinstance(spent, int | float) or spent < 0:
        return "-"
    return f"~{format_usd(spent)}" if spent else "-"


def sessions_table(infos: list[Any], current: str, now: float) -> Table:
    """Saved sessions, newest first; the short id works with /resume and --resume."""
    table = Table(title="Sessions in this folder", title_style="bold", show_edge=False)
    table.add_column("id", style="yellow")
    table.add_column("last used", style="green")
    table.add_column("turns", justify="right", style="cyan")
    table.add_column("est. cost", justify="right", style="green")
    table.add_column("first message")
    for info in infos:
        marker = " *" if info.session_id == current else ""
        age = time_ago(now - info.updated) if info.updated else "older"
        turns = str(info.turns) if info.updated else "?"
        table.add_row(
            info.session_id[:8] + marker,
            age,
            turns,
            _session_cost(info),
            Text(info.title or "(no title)"),
        )
    return table


def mcp_table(servers: dict[str, Any], connected: list[str]) -> Table | Text:
    """Saved MCP servers and which are connected in this chat."""
    from code_harness.mcp_registry import describe

    if not servers and not connected:
        return Text("No saved MCP servers. Add one: edge-harness --mcp-add server.json")
    table = Table(title="MCP servers", title_style="bold", show_edge=False)
    table.add_column("name", style="cyan")
    table.add_column("state")
    table.add_column("definition")
    for name, server in servers.items():
        state = Text("connected", "green") if name in connected else Text("saved", "dim")
        table.add_row(Text(name), state, Text(describe(server)))
    for name in connected:
        if name not in servers:
            table.add_row(Text(name), Text("connected", "green"), Text("from --mcp-config"))
    return table


def skills_table(skills: list[Any], selected: list[str]) -> Table | Text:
    """Skills available by name, marking those loaded in this chat."""
    if not skills:
        return Text(
            "No skills found. Add .agents/skills/<name>/SKILL.md here, "
            "or in the skills folder of ~/.edge-harness."
        )
    table = Table(title="Skills", title_style="bold", show_edge=False)
    table.add_column("name", style="cyan")
    table.add_column("from", style="green")
    table.add_column("state")
    table.add_column("description")
    for skill in skills:
        state = Text("loaded", "green") if skill.name in selected else Text("-", "dim")
        table.add_row(Text(skill.name), skill.where, state, Text(skill.description))
    return table


def time_ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 1440:
        return f"{minutes // 60}h ago"
    return f"{minutes // 1440}d ago"


SLASH_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "list commands"),
    ("/model", "show the model and route; /model <id> switches"),
    ("/context", "context used vs the model's window"),
    ("/usage", "session token totals"),
    ("/cost", "estimated session cost by model"),
    ("/compact", "summarize older messages now"),
    ("/plan", "show the task plan"),
    ("/changes", "files changed this session"),
    ("/image", "/image <path> [message] attaches a picture"),
    ("/skills", "list skills in this repo and your global folder"),
    ("/skill", "/skill <name> loads a skill into this chat"),
    ("/mcp", "saved MCP servers; /mcp connect <name> connects one"),
    ("/approve", "show approvals"),
    ("/approve ask", "ask every time again"),
    ("/sessions", "list saved sessions"),
    ("/resume", "/resume <id> switches session"),
    ("/new", "start a new session"),
    ("/clear", "start a new session"),
    ("/status", "workspace and session id"),
    ("/exit", "leave"),
    ("/quit", "leave"),
)


def slash_completer() -> Any:
    """Menu of slash commands; only active while the input starts with `/`."""
    from prompt_toolkit.completion import Completer, Completion

    class SlashCompleter(Completer):
        def get_completions(self, document: Any, complete_event: Any) -> Any:
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            for command, description in SLASH_COMMANDS:
                if command.startswith(text):
                    yield Completion(command, start_position=-len(text), display_meta=description)

    return SlashCompleter()
