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
