"""Rich renderables for the chat terminal: diffs, commands, tool calls and status lines.

All model- or repository-supplied text is rendered as plain text (no markup), so it cannot
inject styles or fake prompts.
"""

from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

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


def review_renderable(text: str) -> RenderableType:
    """Turn an approval display message into a panel; anything else stays plain text."""
    if text.startswith(DIFF_PREFIX):
        head, _, diff = text.partition("\n")
        return diff_panel(head.removeprefix(DIFF_PREFIX), diff)
    if text.startswith(COMMAND_PREFIX):
        lines = text.splitlines()
        if len(lines) >= 2:
            return command_panel("\n".join(lines[1:-1]) or lines[1])
    if text.startswith("MCP "):
        return Panel(Text(text), title="MCP", border_style="magenta", expand=True)
    return Text(text, style="dim" if text.startswith(("Applied under", "Run under")) else "")


def changes_table(changes: list[tuple[str, int, int, str]], title: str) -> Table:
    """Approved file changes with line counts; paths are shown literally."""

    table = Table(title=title, title_style="bold", show_edge=False, pad_edge=False)
    table.add_column("file")
    table.add_column("change")
    table.add_column("+", justify="right", style="green")
    table.add_column("-", justify="right", style="red")
    for path, added, removed, kind in changes:
        table.add_row(Text(path), kind, str(added), str(removed))
    return table


def session_header(*, workspace: str, model: str, session_id: str, guidance: list[str]) -> Panel:
    """Start-of-chat summary: where we are, which model, how approvals work."""
    lines = Text()
    lines.append("workspace  ", style="dim")
    lines.append(workspace + "\n")
    lines.append("model      ", style="dim")
    lines.append(model + "\n")
    lines.append("session    ", style="dim")
    lines.append(f"Session: {session_id}\n")
    if guidance:
        lines.append("guidance   ", style="dim")
        lines.append(", ".join(guidance) + "\n")
    lines.append(
        "Edits and commands ask first: y = once, a = rest of session, n = no. "
        "/help lists commands. Conversations are stored locally.",
        style="dim",
    )
    return Panel(lines, title=Text("edge-harness", style="bold"), border_style="green")


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
        parts.append((f"fg:{color}", f"Context {left}% left"))
        parts.append(("fg:ansiwhite", f"{short_tokens(used)} / {short_tokens(context_limit)}"))
    parts.append(("fg:ansiblue", f"{short_tokens(input_tokens)} in"))
    if cached_tokens:
        parts.append(("fg:ansimagenta", f"{short_tokens(cached_tokens)} cached"))
    parts.append(("fg:ansigreen", f"{short_tokens(output_tokens)} out"))
    parts.append(("fg:ansibrightblack", "/help"))
    segments: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        if index:
            segments.append(separator)
        segments.append(part)
    return segments
