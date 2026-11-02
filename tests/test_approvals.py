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
        proposal, interactive=True, display=shown.append, read_reply=lambda _: "y"
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
