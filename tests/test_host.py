import inspect
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from test_subagents import Script, run_turn, spawn

from code_harness import host
from code_harness.agent import create_agent
from code_harness.approvals import SessionApprovals, review_host
from code_harness.config import Settings
from code_harness.host import command_environment, run_command, validate_command
from code_harness.ui import review_renderable


def tool_call(agent: Any, command: str) -> str:
    context = MagicMock()
    context.cancel_signal = threading.Event()
    return agent.tool_registry.registry["run_on_host"]._tool_func(command, tool_context=context)


def make_agent(tmp_path: Path, approve: bool = True, notes: list[str] | None = None) -> Any:
    review = MagicMock(return_value=approve)
    notify = notes.append if notes is not None else lambda text: None
    agent = create_agent(
        Settings(workspace=tmp_path),
        model=Script(main=[]).model(),
        review_host=review,
        notify=notify,
    )
    return agent, review


PY = f'"{sys.executable}" -c'


def test_approved_command_runs_in_the_real_workspace(tmp_path: Path) -> None:
    notes: list[str] = []
    agent, review = make_agent(tmp_path, notes=notes)
    command = f"{PY} \"open('made.txt','w').write('x');print('hello')\""
    result = tool_call(agent, command)
    review.assert_called_once_with(command)
    assert (tmp_path / "made.txt").read_text() == "x"
    assert "exit code 0" in result and "hello" in result
    assert notes == ["run_on_host: exit code 0\n  hello"]  # The user sees the real result.


def test_rejected_command_never_runs(tmp_path: Path) -> None:
    agent, _ = make_agent(tmp_path, approve=False)
    result = tool_call(agent, f"{PY} \"open('made.txt','w')\"")
    assert "did not approve" in result
    assert not (tmp_path / "made.txt").exists()


def test_failure_exit_code_is_reported(tmp_path: Path) -> None:
    agent, _ = make_agent(tmp_path)
    assert "exit code 3" in tool_call(agent, f'{PY} "import sys;sys.exit(3)"')


@pytest.mark.parametrize(
    "command",
    ["", "   ", "a\nb", "git push\rrm x", "safe \x1b[2K", "safe\u202erm", "safe\u200b", "x" * 5000],
)
def test_disguised_commands_are_refused_before_review(tmp_path: Path, command: str) -> None:
    agent, review = make_agent(tmp_path)
    assert "not run" in tool_call(agent, command)
    review.assert_not_called()


def test_validate_keeps_a_normal_command() -> None:
    assert validate_command('  git commit -m "first"  ') == 'git commit -m "first"'


def _replying(reply: str) -> Any:
    return lambda text: reply


def test_only_a_typed_yes_approves() -> None:
    for reply in ["y", "a", "all", "", "no", "yes please"]:
        assert not review_host(
            "git push",
            interactive=True,
            display=lambda text: None,
            read_reply=_replying(reply),
        )
    assert review_host(
        "git push", interactive=True, display=lambda text: None, read_reply=lambda text: " YES "
    )


def test_standing_approvals_do_not_cover_it_and_no_terminal_refuses() -> None:
    # The function takes no session at all, so `a` can never grant a standing approval.
    assert "session" not in inspect.signature(review_host).parameters
    SessionApprovals(edits=True, commands=True)
    assert not review_host(
        "git push", interactive=True, display=lambda text: None, read_reply=lambda text: "a"
    )
    assert not review_host(
        "git push", interactive=False, display=print, read_reply=lambda text: "yes"
    )


def test_eof_and_interrupt_refuse() -> None:
    def ended(text: str) -> str:
        raise EOFError

    assert not review_host("x", interactive=True, display=lambda text: None, read_reply=ended)


def test_prompt_states_the_real_risk() -> None:
    shown: list[str] = []
    review_host("git push", interactive=True, display=shown.append, read_reply=lambda text: "no")
    assert "REAL" in shown[0] and "git push" in shown[0] and "not sandboxed" in shown[0].lower()
    assert review_renderable(shown[0]).title == "REAL machine"


def test_subagents_never_get_it(tmp_path: Path) -> None:
    roles = ["explore", "review", "edit"]
    script = Script(main=[[spawn(role) for role in roles], "ok"], sub=["a", "b", "c"])
    agent = create_agent(
        Settings(workspace=tmp_path),
        model=script.model(),
        review=MagicMock(return_value=False),
        review_host=MagicMock(return_value=False),
    )
    assert "run_on_host" in agent.tool_registry.registry
    run_turn(agent)
    assert len(script.sub_tools) == 3
    assert not any("run_on_host" in names for names in script.sub_tools)


def test_needs_review_and_can_be_disabled(tmp_path: Path) -> None:
    model = Script(main=[]).model()
    agent = create_agent(Settings(workspace=tmp_path), model=model)
    assert "run_on_host" not in agent.tool_registry.registry
    settings = Settings(workspace=tmp_path, harness_enable_host_commands=False)
    agent = create_agent(settings, model=model, review_host=MagicMock())
    assert "run_on_host" not in agent.tool_registry.registry


def test_secrets_are_not_passed_to_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "GH_TOKEN", "MY_API_KEY", "DB_PASSWORD"]:
        monkeypatch.setenv(name, "hidden")
    monkeypatch.setenv("HARMLESS_SETTING", "kept")
    env = command_environment()
    assert "HARMLESS_SETTING" in env and "PATH" in {name.upper() for name in env}
    assert not any(value == "hidden" for value in env.values())
    assert env["GIT_TERMINAL_PROMPT"] == "0"


SLEEPER = f'{PY} "import time;time.sleep(60)"'


def test_timeout_stops_the_process_tree(tmp_path: Path) -> None:
    started = time.monotonic()
    code, _, problem = run_command(SLEEPER, tmp_path, None, timeout=1)
    assert code is None and "stopped after 1 seconds" in problem
    assert time.monotonic() - started < 20


def test_cancel_stops_the_command(tmp_path: Path) -> None:
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    code, _, problem = run_command(SLEEPER, tmp_path, cancel, timeout=60)
    assert code is None and problem == "cancelled"


def test_command_cannot_wait_on_stdin(tmp_path: Path) -> None:
    code, output, _ = run_command(
        f'{PY} "import sys;print(repr(sys.stdin.read()))"', tmp_path, None, timeout=20
    )
    assert code == 0 and "''" in output


def test_output_is_cleaned_and_keeps_the_end() -> None:
    assert host._clean("\x1b[31mred\x1b[0m\r\nok\x07") == "red\nok"
    long = "a" * (host.MAX_OUTPUT_CHARS + 10) + "END"
    cleaned = host._clean(long)
    assert cleaned.endswith("END") and cleaned.startswith("[Earlier output truncated.]")


def test_real_git_commit_and_push_to_local_remote(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    agent, _ = make_agent(repo)
    (repo / "a.txt").write_text("one\n")
    for command in [
        "git init -q -b main",
        "git config user.name t",
        "git config user.email t@example.invalid",
        "git add a.txt",
        'git commit -qm "first commit"',
        f'git remote add origin "{bare.as_posix()}"',
        "git push -u origin main",
    ]:
        result = tool_call(agent, command)
        assert "exit code 0" in result, (command, result)
    log = subprocess.run(
        ["git", "--git-dir", str(bare), "log", "--format=%s", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "first commit"


def test_a_slip_asks_again_and_never_approves() -> None:
    replies = iter(["y", "yes"])
    shown: list[str] = []
    assert review_host(
        "git push", interactive=True, display=shown.append, read_reply=lambda text: next(replies)
    )
    assert any("full word yes" in text for text in shown)

    replies = iter(["y", "ok", "sure", "yes"])
    shown.clear()
    assert not review_host(
        "git push", interactive=True, display=shown.append, read_reply=lambda text: next(replies)
    )
    assert "did not run" in shown[-1]


@pytest.mark.parametrize("reply", ["", "n", "no"])
def test_a_clear_no_rejects_at_once_and_says_so(reply: str) -> None:
    asked: list[str] = []
    shown: list[str] = []

    def read(text: str) -> str:
        asked.append(text)
        return reply

    assert not review_host("git push", interactive=True, display=shown.append, read_reply=read)
    assert len(asked) == 1 and "did not run" in shown[-1]


def test_only_the_first_command_of_a_response_runs_in_order(tmp_path: Path) -> None:
    calls = [("run_on_host", {"command": f'{PY} "print({n})"'}) for n in range(5)]
    script = Script(main=[calls, "ok"])
    review = MagicMock(return_value=True)
    notes: list[str] = []
    agent = create_agent(
        Settings(workspace=tmp_path), model=script.model(), review_host=review, notify=notes.append
    )
    run_turn(agent)
    review.assert_called_once_with(calls[0][1]["command"])
    assert notes == ["run_on_host: exit code 0\n  0"]
    assert script.main_requests[-1].count("only the first run_on_host") == 4


def test_user_sees_the_end_of_failed_output_cleanly(tmp_path: Path) -> None:
    notes: list[str] = []
    agent, _ = make_agent(tmp_path, notes=notes)
    command = f'{PY} "import sys;[print(i) for i in range(20)];print(chr(27)+chr(91)+chr(50)+chr(74)+chr(102));sys.exit(1)"'
    tool_call(agent, command)
    assert notes[0].startswith("run_on_host: exit code 1")
    lines = notes[0].splitlines()
    assert len(lines) == 1 + host.MAX_SHOWN_LINES and lines[-1] == "  f"
    assert "\x1b" not in notes[0]
