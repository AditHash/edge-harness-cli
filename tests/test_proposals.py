from pathlib import Path

import pytest

from code_harness.proposals import propose
from code_harness.workspace import Workspace, WorkspaceError


def test_proposal_does_not_change_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old\n")
    proposal = propose(Workspace(tmp_path), "app.py", "new\n")
    assert "-old" in proposal.diff and "+new" in proposal.diff
    assert proposal.original_digest is not None
    assert target.read_text() == "old\n"


def test_proposals_protect_paths_and_support_create_delete(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    assert "/dev/null" in propose(workspace, "new.py", "x\n").diff
    (tmp_path / "old.py").write_text("x\n")
    assert "/dev/null" in propose(workspace, "old.py", None).diff
    with pytest.raises(WorkspaceError):
        propose(workspace, ".env", "secret")
    with pytest.raises(WorkspaceError):
        propose(workspace, "../outside", "x")


def test_diff_marks_missing_terminal_newline(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_bytes(b"old")
    proposal = propose(Workspace(tmp_path), "file.txt", "new")
    assert "-old\n\\ No newline at end of file\n" in proposal.diff
    assert "+new\n\\ No newline at end of file\n" in proposal.diff
