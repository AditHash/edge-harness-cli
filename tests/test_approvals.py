from pathlib import Path

import pytest

from code_harness.approvals import review_proposal
from code_harness.proposals import propose
from code_harness.workspace import Workspace, WorkspaceError


def test_only_explicit_yes_approves_and_diff_is_shown(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n")
    proposal = propose(Workspace(tmp_path), "app.py", "new\n")
    shown: list[str] = []
    assert review_proposal(
        proposal, interactive=True, display=shown.append, read_reply=lambda _: "yes"
    )
    assert proposal.diff in shown[0]
    assert not review_proposal(
        proposal, interactive=True, display=shown.append, read_reply=lambda _: ""
    )
    assert not review_proposal(
        proposal, interactive=False, display=shown.append, read_reply=lambda _: "yes"
    )
    assert (tmp_path / "app.py").read_text() == "old\n"


def test_eof_cancel_and_terminal_escape_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n")
    proposal = propose(Workspace(tmp_path), "app.py", "new\n")

    def eof(_: str) -> str:
        raise EOFError

    assert not review_proposal(proposal, interactive=True, display=lambda _: None, read_reply=eof)
    unsafe = propose(Workspace(tmp_path), "app.py", "\x1b[2Jhidden")
    with pytest.raises(WorkspaceError, match="control characters"):
        review_proposal(
            unsafe, interactive=True, display=lambda _: None, read_reply=lambda _: "yes"
        )


def test_all_grants_session_approval_for_edits_only(tmp_path: Path) -> None:
    from code_harness.approvals import SessionApprovals, review_command

    (tmp_path / "app.py").write_text("old\n")
    proposal = propose(Workspace(tmp_path), "app.py", "new\n")
    session = SessionApprovals()
    shown: list[str] = []
    asked: list[str] = []

    def reply(prompt: str) -> str:
        asked.append(prompt)
        return "all"

    assert review_proposal(
        proposal, interactive=True, display=shown.append, read_reply=reply, session=session
    )
    assert session.edits and not session.commands
    assert asked[0] == "Apply to app.py? [y]es / [a]ll this session / [n]o: "
    # Later edits: diff still shown, no question asked.
    assert review_proposal(
        proposal,
        interactive=True,
        display=shown.append,
        read_reply=lambda _: pytest.fail("asked again"),
        session=session,
    )
    assert sum(proposal.diff in text for text in shown) == 2
    # Commands still ask until approved separately.
    assert not review_command(
        "ls", interactive=True, display=shown.append, read_reply=lambda _: "no", session=session
    )
    # Never approves without a user, even with a standing approval.
    assert not review_proposal(
        proposal, interactive=False, display=shown.append, read_reply=reply, session=session
    )
    session.reset()
    assert not session.edits and "ask each time" in session.describe()


def test_all_without_session_does_not_approve(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("old\n")
    proposal = propose(Workspace(tmp_path), "app.py", "new\n")
    assert not review_proposal(
        proposal, interactive=True, display=lambda _: None, read_reply=lambda _: "all"
    )


def test_command_session_approval(tmp_path: Path) -> None:
    from code_harness.approvals import SessionApprovals, review_command

    session = SessionApprovals()
    assert review_command(
        "pytest",
        interactive=True,
        display=lambda _: None,
        read_reply=lambda _: "all",
        session=session,
    )
    assert session.commands and not session.edits
    assert review_command(
        "pytest -x",
        interactive=True,
        display=lambda _: None,
        read_reply=lambda _: pytest.fail("asked"),
        session=session,
    )


def test_chat_approve_command_reports_and_clears(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from code_harness import cli

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(cli, "_make_agent", lambda *a, **k: object())
    cli.SESSION_APPROVALS.edits = True  # left over from an earlier chat
    result = CliRunner().invoke(
        cli.app, ["chat", "--workspace", str(workspace)], input="/approve\n/approve ask\n/exit\n"
    )
    assert result.exit_code == 0, result.output
    assert "File changes: ask each time" in result.output  # reset when the chat started
    assert "Standing approvals cleared" in result.output


@pytest.mark.parametrize(
    ("reply", "approved", "standing"),
    [
        ("y", True, False),
        ("YES", True, False),
        ("a", True, True),
        (" all ", True, True),
        ("n", False, False),
        ("", False, False),
        ("yep", False, False),
        ("[y]", False, False),
    ],
)
def test_reply_parsing(tmp_path: Path, reply: str, approved: bool, standing: bool) -> None:
    from code_harness.approvals import SessionApprovals

    (tmp_path / "app.py").write_text("old\n")
    proposal = propose(Workspace(tmp_path), "app.py", "new\n")
    session = SessionApprovals()
    assert (
        review_proposal(
            proposal,
            interactive=True,
            display=lambda _: None,
            read_reply=lambda _: reply,
            session=session,
        )
        is approved
    )
    assert session.edits is standing
