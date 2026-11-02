from pathlib import Path

import pytest

from code_harness.workspace import Workspace, WorkspaceError


def test_read_search_and_protected_paths(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("def example():\n    return 42\n")
    (tmp_path / ".env").write_text("secret")
    workspace = Workspace(tmp_path)
    assert "return 42" in workspace.read("source.py")
    assert workspace.search("example") == ["source.py:1: def example():"]
    assert ".env" not in workspace.files()
    for name in (".env", ".npmrc", "file.txt:stream", "../other", str(tmp_path.parent / "other")):
        with pytest.raises(WorkspaceError):
            workspace.read(name)


def test_symlink_and_hardlink_are_denied(tmp_path: Path) -> None:
    source = tmp_path / "file.txt"
    source.write_text("data")
    try:
        (tmp_path / "alias.txt").hardlink_to(source)
    except OSError:
        pytest.skip("hard links are unavailable")
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path).read("alias.txt")


def test_outside_symlink_is_denied(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    try:
        (root / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    workspace = Workspace(root)
    with pytest.raises(WorkspaceError):
        workspace.read("link.txt")
    assert "link.txt" not in workspace.files()


def test_search_skips_dependency_directories_and_reports_truncation(tmp_path: Path) -> None:
    dependency = tmp_path / "node_modules"
    dependency.mkdir()
    (dependency / "noise.js").write_text("needle")
    (tmp_path / "app.py").write_text("needle\n" * 55)
    workspace = Workspace(tmp_path)
    assert all("node_modules" not in path for path in workspace.files())
    results = workspace.search("needle")
    assert len(results) == 51
    assert results[-1] == "[Search results truncated at 50 matches.]"
