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


def test_turn_display_is_compact_and_readable(monkeypatch) -> None:
    import asyncio

    from code_harness import cli
    from code_harness.contracts import AgentEvent

    recorder = Console(width=100, record=True, color_system=None, force_terminal=False)
    monkeypatch.setattr(cli, "console", recorder)
    events = [
        AgentEvent("tool_call", "read_code README.md"),
        AgentEvent("tool_result", "read_code: finished"),
        AgentEvent("tool_call", "git_diff [x]"),
        AgentEvent("tool_result", "git_diff: failed"),
        AgentEvent("call_usage", "in.anthropic.claude-haiku", 1946, 129),
        AgentEvent("text_delta", "## Summary\n\n- **one**"),
        AgentEvent("text_delta", "\n- two\n"),
        AgentEvent("usage", "Input tokens: 2000; output tokens: 150", 2000, 150),
        AgentEvent("turn_completed"),
    ]

    async def fake(agent, prompt):
        for event in events:
            yield event

    monkeypatch.setattr(cli, "stream_turn", fake)
    tracker = cli.UsageTracker()
    asyncio.run(cli._show_turn(object(), "x", tracker))
    text = recorder.export_text()
    assert "* Read README.md" in text or "● Read README.md" in text
    assert "read_code: finished" not in text  # successes stay quiet
    assert "Git diff failed" in text
    assert "Git diff [x]" in text  # markup in tool targets is shown literally
    assert "in.anthropic.claude-haiku  1,946 in / 129 out" in text
    assert "Summary" in text and "## Summary" not in text and "**one**" not in text  # Markdown
    assert "Worked for" in text and "2,000 in / 150 out" in text
    assert tracker.input_tokens == 2000


def test_approved_changes_are_recorded_and_summarized(tmp_path, monkeypatch) -> None:
    import asyncio

    from code_harness import agent as agent_module
    from code_harness import cli
    from code_harness.approvals import SessionApprovals
    from code_harness.contracts import AgentEvent
    from code_harness.proposals import propose
    from code_harness.workspace import Workspace

    recorder = Console(width=100, record=True, color_system=None)
    monkeypatch.setattr(cli, "console", recorder)
    monkeypatch.setattr(cli, "SESSION_CHANGES", [])
    monkeypatch.setattr(cli, "SESSION_APPROVALS", SessionApprovals(edits=True))
    captured = {}
    monkeypatch.setattr(
        agent_module, "create_agent", lambda settings, **kwargs: captured.update(kwargs) or object()
    )
    io = cli.ReviewIO(True, lambda text: None, lambda prompt: "no")
    cli._make_agent(tmp_path, model_id="m", review_io=io)
    (tmp_path / "a.md").write_bytes(b"one\ntwo\n")
    edit = propose(Workspace(tmp_path), "a.md", "one\nTWO\nthree\n")
    created = propose(Workspace(tmp_path), "new.md", "x\n")

    async def fake(agent, prompt):
        assert captured["review"](edit)
        assert captured["review"](created)
        yield AgentEvent("turn_completed")

    monkeypatch.setattr(cli, "stream_turn", fake)
    asyncio.run(cli._show_turn(object(), "x"))
    text = recorder.export_text()
    assert "Changed this turn" in text
    assert "a.md" in text and "edited" in text and "new.md" in text and "created" in text
    assert cli.SESSION_CHANGES == [("a.md", 2, 1, "edited"), ("new.md", 1, 0, "created")]

    cli.SESSION_APPROVALS.reset()
    assert not captured["review"](edit)  # rejected: not recorded
    assert len(cli.SESSION_CHANGES) == 2


def test_markdown_theme_produces_color() -> None:
    import io

    from rich.markdown import Markdown

    stream = io.StringIO()
    console = Console(
        file=stream, theme=ui.THEME, force_terminal=True, color_system="truecolor", width=60
    )
    console.print(Markdown("## Title\n\n- **bold** and `code`", code_theme=ui.CODE_THEME))
    assert "\x1b[" in stream.getvalue() and "Title" in stream.getvalue()


def test_no_color_is_respected(monkeypatch) -> None:
    import io

    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    console = Console(file=stream, theme=ui.THEME, force_terminal=True, width=60)
    console.print("[red]plain[/red]")
    assert "\x1b[31m" not in stream.getvalue()


def test_short_formats() -> None:
    assert ui.short_tokens(14) == "14"
    assert ui.short_tokens(17_500) == "17.5K"
    assert ui.short_tokens(1_500_000) == "1.5M"
    assert ui.short_duration(8.2) == "8s"
    assert ui.short_duration(81) == "1m 21s"


def test_mark_falls_back_on_legacy_encodings() -> None:
    assert ui.mark("utf-8") == "●"
    assert ui.mark("cp1252") == "*"
    assert ui.mark(None) == "*"


def test_user_bar_and_bullet_row_render_literal_text() -> None:
    assert "> [red]hi[/red]" in _render(ui.user_bar("[red]hi[/red]"))
    wide = _render(ui.bullet_row("word " * 40, "*", "white")).splitlines()
    assert wide[0].startswith("* word") and wide[1].startswith("  word")


def test_status_segments_show_context_left_and_tokens() -> None:
    segments = ui.status_segments(
        model="in.moonshotai.kimi-k3",
        folder="v2v",
        approvals="ask",
        context_used=20_000,
        context_limit=1_000_000,
        input_tokens=30_700,
        cached_tokens=2_000,
        output_tokens=14,
    )
    text = "".join(part for _, part in segments)
    for expected in (
        "in.moonshotai.kimi-k3",
        "v2v",
        "Context 98% left",
        "20.0K / 1.0M",
        "30.7K in",
        "2.0K cached",
        "14 out",
    ):
        assert expected in text
    low = ui.status_segments(
        model="m",
        folder="f",
        approvals="ask",
        context_used=95,
        context_limit=100,
        input_tokens=0,
        cached_tokens=0,
        output_tokens=0,
    )
    assert ("fg:ansired", "Context 5% left") in low
    assert "cached" not in "".join(part for _, part in low)


def test_status_uses_live_model_and_context(tmp_path) -> None:
    from types import SimpleNamespace

    from code_harness import cli
    from code_harness.config import Settings
    from code_harness.usage import UsageTracker

    tracker = UsageTracker()
    tracker.add(1000, 50, 200)
    agent = SimpleNamespace(
        model=SimpleNamespace(
            context_window_limit=1_000,
            get_config=lambda: {"model_id": "in.moonshotai.kimi-k3"},
        ),
        event_loop_metrics=SimpleNamespace(latest_context_size=100),
    )
    text = "".join(
        part for _, part in cli._status(agent, Settings(workspace=tmp_path), "auto", tracker)
    )
    assert "auto: in.moonshotai.kimi-k3" in text
    assert tmp_path.name in text
    assert "Context 90% left" in text and "1.0K in" in text and "200 cached" in text


def test_piped_input_reads_lines_without_prompt_toolkit(monkeypatch) -> None:
    import io
    import sys

    from code_harness import cli

    monkeypatch.setattr(sys, "stdin", io.StringIO("  hello  \n"))
    assert cli._read_prompt(list) == "hello"


def test_terminal_prompt_reads_input_and_renders_status(monkeypatch) -> None:
    import sys

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from code_harness import cli

    monkeypatch.setattr(cli, "_prompt_session", None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    asked: list[int] = []

    def status() -> list[tuple[str, str]]:
        asked.append(1)
        return [("fg:ansicyan", "model")]

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text("  hello world \r")
        assert cli._read_prompt(status) == "hello world"
    assert asked  # the status bar was rendered


def test_slash_completer_filters_and_describes() -> None:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    completer = ui.slash_completer()

    def names(text: str) -> list[str]:
        return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]

    assert len(names("/")) == len(ui.SLASH_COMMANDS)
    assert names("/mo") == ["/model"]
    assert names("/approve") == ["/approve", "/approve ask"]
    assert names("hello") == [] and names("/nope") == []


def test_every_hinted_command_is_handled_by_chat() -> None:
    from pathlib import Path

    from code_harness import cli

    source = Path(cli.__file__).read_text(encoding="utf-8")
    for command, _ in ui.SLASH_COMMANDS:
        assert f'"{command}"' in source or f'"{command} "' in source, command


def test_tab_completes_a_slash_command_at_the_terminal(monkeypatch) -> None:
    import sys
    import threading
    import time

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from code_harness import cli

    monkeypatch.setattr(cli, "_prompt_session", None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        # Like a person: type, see the menu appear, press Tab, then Enter.
        def keys() -> None:
            pipe.send_text("/cha")
            time.sleep(0.4)
            menu = cli._prompt_session.default_buffer.complete_state
            shown.extend(item.text for item in menu.completions if menu)
            pipe.send_text("\t")
            time.sleep(0.2)
            pipe.send_text("\r")

        shown: list[str] = []
        typist = threading.Thread(target=keys)
        typist.start()
        try:
            assert cli._read_prompt(list) == "/changes"
        finally:
            typist.join()
        assert shown == ["/changes"]


def test_sessions_table_shows_short_id_age_and_title() -> None:
    from code_harness.sessions import SessionInfo

    infos = [
        SessionInfo("a" * 32, "[red]fix bug[/red]", 1000.0, 3),
        SessionInfo("b" * 32, "", 0.0, 0),
    ]
    text = _render(ui.sessions_table(infos, "a" * 32, 1000.0 + 7200))
    assert "aaaaaaaa *" in text and "2h ago" in text and "[red]fix bug[/red]" in text
    assert "bbbbbbbb" in text and "older" in text and "(no title)" in text
    assert ui.time_ago(5) == "just now" and ui.time_ago(300) == "5m ago"
    assert ui.time_ago(172800) == "2d ago"
