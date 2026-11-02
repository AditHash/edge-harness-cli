"""Fixed-argument, read-only Git inspection for the selected workspace."""

import os
import shutil
import subprocess
from pathlib import Path

from code_harness.workspace import PROTECTED, SECRET_SUFFIXES, Workspace, WorkspaceError

MAX_GIT_OUTPUT = 64 * 1024
GIT_TIMEOUT_SECONDS = 20

# Pathspec exclusions keep protected files out of diffs even when tracked.
_EXCLUDES = [
    *(f":(exclude,glob)**/{name}" for name in sorted(PROTECTED)),
    *(f":(exclude,glob)**/{name}/**" for name in sorted(PROTECTED)),
    ":(exclude,glob)**/.env",
    ":(exclude,glob)**/.env.*",
    *(f":(exclude,glob)**/*{suffix}" for suffix in sorted(SECRET_SUFFIXES)),
]

# Repository config can name programs (fsmonitor, textconv, external diff, pager);
# each is disabled so reading status or a diff never runs repository-supplied code.
_SAFE_OPTIONS = [
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.pager=cat",
    "-c",
    "core.hooksPath=NUL" if os.name == "nt" else "core.hooksPath=/dev/null",
    "-c",
    "diff.external=",
    "-c",
    "core.quotePath=false",
    "--no-pager",
    "--no-optional-locks",
]


def _git() -> str:
    found = shutil.which("git")
    if found is None:
        raise WorkspaceError("Git is not installed or not on PATH")
    return found


def _run(workspace: Workspace, args: list[str]) -> str:
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE"}
    }
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    try:
        done = subprocess.run(
            [_git(), *_SAFE_OPTIONS, "-C", str(workspace.root), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise WorkspaceError("Git inspection failed or timed out") from None
    if done.returncode != 0:
        raise WorkspaceError("Git inspection failed; is the workspace a trusted Git repository?")
    text = done.stdout[: MAX_GIT_OUTPUT + 1].decode("utf-8", errors="replace")
    if len(text) > MAX_GIT_OUTPUT:
        text = text[:MAX_GIT_OUTPUT] + "\n[Output truncated.]"
    return text


def git_status(workspace: Workspace) -> str:
    """Short branch and change summary, without protected paths."""
    return _run(workspace, ["status", "--short", "--branch", "--", ".", *_EXCLUDES]) or "Clean."


def git_diff(workspace: Workspace, path: str | None = None, staged: bool = False) -> str:
    """Unified diff of working-tree or staged changes, optionally for one path."""
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    if staged:
        args.append("--cached")
    target = "."
    if path:
        resolved = (workspace.root / path).resolve()
        try:
            relative = resolved.relative_to(workspace.root)
        except ValueError:
            raise WorkspaceError("Path is outside the workspace") from None
        target = Path(relative).as_posix()
    return _run(workspace, [*args, "--", target, *_EXCLUDES]) or "No differences."
