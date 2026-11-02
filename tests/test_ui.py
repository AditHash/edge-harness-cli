from rich.console import Console
from rich.panel import Panel

from code_harness import ui


def _render(renderable) -> str:
    console = Console(width=100, record=True, color_system=None)
    console.print(renderable)
    return console.export_text()


DIFF = "--- a/README.md\n+++ b/README.md\n@@ -1,2 +1,2 @@\n-old line\n+new line\n+another\n same\n"


def test_diff_stats_ignore_file_headers() -> None:
    assert ui.diff_stats(DIFF) == (2, 1)


def test_diff_panel_shows_path_counts_and_lines() -> None:
    text = _render(ui.review_renderable(f"{ui.DIFF_PREFIX}README.md\n{DIFF}"))
    assert "README.md" in text and "+2" in text and "-1" in text
    assert "-old line" in text and "+new line" in text


def test_command_panel() -> None:
    message = (
        "Proposed sandbox command (copied workspace, no network):\n"
        "python3 -m pytest -q\nSandbox changes do not modify the live workspace."
    )
    renderable = ui.review_renderable(message)
    assert isinstance(renderable, Panel)
    text = _render(renderable)
    assert "python3 -m pytest -q" in text and "no network" in text


def test_untrusted_markup_is_not_interpreted() -> None:
    text = _render(ui.review_renderable(f"{ui.DIFF_PREFIX}[bold]x[/bold].md\n+[red]hi[/red]\n"))
    assert "[bold]x[/bold].md" in text and "[red]hi[/red]" in text
    assert _render(ui.review_renderable("[blink]plain[/blink]")).strip() == "[blink]plain[/blink]"
