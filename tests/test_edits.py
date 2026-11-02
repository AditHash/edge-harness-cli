import os
from pathlib import Path

import pytest

from code_harness import edits
from code_harness.edits import apply_reviewed_edit
from code_harness.proposals import propose
from code_harness.workspace import Workspace, WorkspaceError

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="exclusive edit backend currently requires Windows"
)


def test_approved_rejected_and_stale_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"old\n")
    workspace = Workspace(root)
    proposal = propose(workspace, "app.py", "new\n")
    assert "rejected" in apply_reviewed_edit(workspace, proposal, lambda _: False)
    assert target.read_bytes() == b"old\n"

    def change_during_review(_):
        target.write_bytes(b"other\n")
        return True

    with pytest.raises(WorkspaceError, match="changed after review"):
        apply_reviewed_edit(workspace, proposal, change_during_review)
    assert target.read_bytes() == b"other\n"
    proposal = propose(workspace, "app.py", "new\n")
    assert "Applied" in apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert target.read_bytes() == b"new\n"
    assert list((tmp_path / ".edge-harness" / "recovery").iterdir()) == []


def test_directory_and_ancestor_cannot_be_renamed_during_edit(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    root = parent / "repo"
    root.mkdir(parents=True)
    with edits._locked_directories(root):
        with pytest.raises(OSError):
            root.rename(parent / "moved")
        with pytest.raises(OSError):
            parent.rename(tmp_path / "moved-parent")
    assert root.is_dir()


def test_existing_open_handle_prevents_exclusive_edit(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_bytes(b"old\n")
    workspace = Workspace(tmp_path)
    proposal = propose(workspace, "app.py", "new\n")
    with target.open("rb"), pytest.raises(WorkspaceError, match="exclusive access"):
        apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert target.read_bytes() == b"old\n"


def test_failed_write_restores_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"original\n")
    workspace = Workspace(root)
    proposal = propose(workspace, "app.py", "replacement\n")
    real_write = edits._write_all
    calls = 0

    def fail_once(stream, content):
        nonlocal calls
        calls += 1
        if calls == 1:
            stream.write(b"partial")
            raise OSError("simulated failure")
        real_write(stream, content)

    monkeypatch.setattr(edits, "_write_all", fail_once)
    with pytest.raises(WorkspaceError, match="original file was restored"):
        apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert target.read_bytes() == b"original\n"
    assert list((tmp_path / ".edge-harness" / "recovery").iterdir()) == []


def test_failed_rollback_retains_recovery_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"original\n")
    workspace = Workspace(root)
    proposal = propose(workspace, "app.py", "replacement\n")

    def fail_write(stream, content):
        raise OSError("simulated failure")

    monkeypatch.setattr(edits, "_write_all", fail_write)
    with pytest.raises(WorkspaceError, match="original bytes are saved"):
        apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert len(list((tmp_path / ".edge-harness" / "recovery").iterdir())) == 1


def test_creation_requires_approval_and_never_overwrites_racing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    workspace = Workspace(root)
    target = root / "new.txt"
    proposal = propose(workspace, "new.txt", "new")
    assert "rejected" in apply_reviewed_edit(workspace, proposal, lambda _: False)
    assert not target.exists()

    def race(_):
        target.write_text("concurrent")
        return True

    with pytest.raises(WorkspaceError, match="exclusive access"):
        apply_reviewed_edit(workspace, proposal, race)
    assert target.read_text() == "concurrent"
    target.unlink()
    assert "Created" in apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert target.read_text() == "new"


def test_failed_creation_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    workspace = Workspace(root)
    proposal = propose(workspace, "new.txt", "new")

    def fail_write(stream, content):
        stream.write(b"partial")
        raise OSError("simulated failure")

    monkeypatch.setattr(edits, "_write_all", fail_write)
    with pytest.raises(WorkspaceError, match="new file was removed"):
        apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert not (root / "new.txt").exists()


def test_directory_swap_after_review_does_not_change_outside_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    folder = root / "src"
    folder.mkdir()
    (folder / "app.py").write_bytes(b"old\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "app.py"
    outside_file.write_bytes(b"old\n")
    workspace = Workspace(root)
    proposal = propose(workspace, "src/app.py", "new\n")

    def swap(_):
        folder.rename(root / "original")
        try:
            folder.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlink creation unavailable")
        return True

    with pytest.raises(WorkspaceError):
        apply_reviewed_edit(workspace, proposal, swap)
    assert outside_file.read_bytes() == b"old\n"
    assert (root / "original" / "app.py").read_bytes() == b"old\n"


def test_deletion_requires_approval_and_rejects_stale_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"old\n")
    workspace = Workspace(root)
    proposal = propose(workspace, "app.py", None)
    assert "rejected" in apply_reviewed_edit(workspace, proposal, lambda _: False)
    assert target.read_bytes() == b"old\n"

    def modify(_):
        target.write_bytes(b"changed\n")
        return True

    with pytest.raises(WorkspaceError, match="changed after review"):
        apply_reviewed_edit(workspace, proposal, modify)
    assert target.read_bytes() == b"changed\n"
    proposal = propose(workspace, "app.py", None)
    assert "Deleted" in apply_reviewed_edit(workspace, proposal, lambda _: True)
    assert not target.exists()
