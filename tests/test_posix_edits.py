import os
import tempfile
from pathlib import Path

import pytest

from code_harness.edits import apply_reviewed_edit
from code_harness.proposals import propose
from code_harness.workspace import Workspace, WorkspaceError

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX edit backend")


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def test_edit_create_delete_roundtrip(root: Path) -> None:
    ws = Workspace(root)
    (root / "a.txt").write_text("old\n")
    assert "Applied" in apply_reviewed_edit(ws, propose(ws, "a.txt", "new\n"), lambda p: True)
    assert (root / "a.txt").read_text() == "new\n"
    (root / "sub").mkdir()
    apply_reviewed_edit(ws, propose(ws, "sub/b.txt", "hi\n"), lambda p: True)
    assert (root / "sub" / "b.txt").read_text() == "hi\n"
    apply_reviewed_edit(ws, propose(ws, "sub/b.txt", None), lambda p: True)
    assert not (root / "sub" / "b.txt").exists()


def test_rejection_and_stale_review_leave_bytes_unchanged(root: Path) -> None:
    ws = Workspace(root)
    target = root / "a.txt"
    target.write_text("old\n")
    assert "rejected" in apply_reviewed_edit(ws, propose(ws, "a.txt", "new\n"), lambda p: False)
    assert target.read_text() == "old\n"

    def swap_then_approve(proposal) -> bool:
        target.write_text("changed\n")
        return True

    with pytest.raises(WorkspaceError):
        apply_reviewed_edit(ws, propose(ws, "a.txt", "new\n"), swap_then_approve)
    assert target.read_text() == "changed\n"


def test_symlinked_parent_after_review_cannot_redirect_write(root: Path, tmp_path: Path) -> None:
    ws = Workspace(root)
    (root / "d").mkdir()
    (root / "d" / "f.txt").write_text("old\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("outside\n")
    proposal = propose(ws, "d/f.txt", "new\n")

    def swap(p) -> bool:
        (root / "d" / "f.txt").unlink()
        (root / "d").rmdir()
        os.symlink(outside, root / "d")
        return True

    with pytest.raises(WorkspaceError):
        apply_reviewed_edit(ws, proposal, swap)
    assert (outside / "f.txt").read_text() == "outside\n"


def test_protected_and_traversal_paths_rejected(root: Path) -> None:
    ws = Workspace(root)
    for bad in (".env", "../x.txt", ".git/config", "id_rsa"):
        with pytest.raises(WorkspaceError):
            propose(ws, bad, "x")


def test_hardlink_target_rejected(root: Path, tmp_path: Path) -> None:
    ws = Workspace(root)
    outside = tmp_path / "o.txt"
    outside.write_text("old\n")
    os.link(outside, root / "h.txt")
    with pytest.raises(WorkspaceError):
        apply_reviewed_edit(ws, propose(ws, "h.txt", "new\n"), lambda p: True)
    assert outside.read_text() == "old\n"
    assert tempfile.gettempdir()
