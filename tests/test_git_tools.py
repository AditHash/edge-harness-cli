import subprocess
from pathlib import Path

import pytest

from code_harness.git_tools import git_diff, git_status
from code_harness.workspace import Workspace, WorkspaceError


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("one\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    _git(tmp_path, "add", "-f", "a.py", ".env")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_status_and_diff_hide_protected_files(repo: Path) -> None:
    (repo / "a.py").write_text("two\n")
    (repo / ".env").write_text("SECRET=2\n")
    workspace = Workspace(repo)
    assert "a.py" in git_status(workspace)
    assert ".env" not in git_status(workspace)
    diff = git_diff(workspace)
    assert "+two" in diff and "SECRET" not in diff
    assert git_diff(workspace, "a.py").count("+two") == 1


def test_diff_rejects_outside_path_and_non_repo(repo: Path, tmp_path_factory) -> None:
    with pytest.raises(WorkspaceError):
        git_diff(Workspace(repo), "../outside")
    plain = tmp_path_factory.mktemp("plain")
    with pytest.raises(WorkspaceError):
        git_status(Workspace(plain))


def test_repository_config_cannot_run_programs(repo: Path) -> None:
    marker = repo.parent / "pwned.txt"
    _git(repo, "config", "core.fsmonitor", f"echo x > {marker.as_posix()}")
    _git(repo, "config", "diff.external", f"echo x > {marker.as_posix()}")
    (repo / "a.py").write_text("three\n")
    git_status(Workspace(repo))
    git_diff(Workspace(repo))
    assert not marker.exists()
