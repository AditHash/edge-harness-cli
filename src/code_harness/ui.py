"""Rich renderables for the chat terminal: diffs, commands, tool calls and status lines.

All model- or repository-supplied text is rendered as plain text (no markup), so it cannot
inject styles or fake prompts.
"""

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

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
        "Edits and commands ask first: yes = once, all = rest of session. "
        "/help lists commands. Conversations are stored locally.",
        style="dim",
    )
    return Panel(lines, title=Text("edge-harness", style="bold"), border_style="green")
