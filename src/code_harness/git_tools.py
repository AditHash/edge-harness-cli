"""Fixed-argument, read-only Git inspection for the selected workspace."""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from code_harness.workspace import PROTECTED, SECRET_SUFFIXES, Workspace, WorkspaceError

MAX_GIT_OUTPUT = 64 * 1024
GIT_TIMEOUT_SECONDS = 20
MAX_LOG_COMMITS = 100
MAX_PREVIEW_LINES = 25

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


def _relative_target(workspace: Workspace, path: str | None) -> str:
    if not path:
        return "."
    resolved = (workspace.root / path).resolve()
    try:
        return Path(resolved.relative_to(workspace.root)).as_posix()
    except ValueError:
        raise WorkspaceError("Path is outside the workspace") from None


def git_status(workspace: Workspace) -> str:
    """Short branch and change summary, without protected paths."""
    return _run(workspace, ["status", "--short", "--branch", "--", ".", *_EXCLUDES]) or "Clean."


def git_diff(workspace: Workspace, path: str | None = None, staged: bool = False) -> str:
    """Unified diff of working-tree or staged changes, optionally for one path."""
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    if staged:
        args.append("--cached")
    target = _relative_target(workspace, path)
    return _run(workspace, [*args, "--", target, *_EXCLUDES]) or "No differences."


def git_log(workspace: Workspace, limit: int = 20, path: str | None = None) -> str:
    """Recent commits as hash, relative date, author and subject; no diffs or file contents."""
    count = max(1, min(int(limit), MAX_LOG_COMMITS))
    args = [
        "log",
        f"--max-count={count}",
        "--date=relative",
        "--no-color",
        "--format=%h | %ad | %an | %s",
    ]
    target = _relative_target(workspace, path)
    try:
        return _run(workspace, [*args, "--", target, *_EXCLUDES]) or "No commits."
    except WorkspaceError:
        # `git log` fails on a repository with no commits; status tells those apart.
        if "No commits yet" in git_status(workspace):
            return "No commits yet."
        raise


def _quiet(workspace: Workspace, args: list[str]) -> str:
    try:
        return _run(workspace, args).strip()
    except WorkspaceError:
        return ""


def _printable_lines(text: str, limit: int = MAX_PREVIEW_LINES) -> list[str]:
    lines = ["".join(c for c in line if c.isprintable()) for line in text.splitlines() if line]
    extra = len(lines) - limit
    return [*lines[:limit], f"... and {extra} more"] if extra > 0 else lines


def command_preview(workspace: Workspace, command: str) -> str:
    """What a proposed git command would add or publish, read from the repository itself.

    Shows the user facts the model may have left out. Read-only, with arguments passed directly and
    repository-supplied programs disabled, so nothing runs before the user decides. Any failure
    leaves the preview empty, which never blocks or approves anything.
    """
    sections: list[str] = []
    for part in re.split(r"&&|;|\|\|", command):
        try:
            words = shlex.split(part, posix=True)
        except ValueError:
            continue
        git_at = words.index("git") if "git" in words else -1
        if git_at < 0 or len(words) <= git_at + 1:
            continue
        verb, rest = words[git_at + 1], words[git_at + 2 :]
        if verb == "add":
            everything = any(word in {"-A", "--all"} for word in rest)
            paths = [word for word in rest if not word.startswith("-")]
            if everything or paths:
                listed = _quiet(
                    workspace,
                    ["add", "--dry-run", *(["--all"] if everything else ["--", *paths])],
                )
                names = [re.sub(r"^(add|remove) '(.*)'$", r"\2", x) for x in listed.splitlines()]
                if names:
                    sections.append("Would stage:")
                    sections += [f"  {line}" for line in _printable_lines("\n".join(names))]
        elif verb == "push":
            remote = next((word for word in rest if not word.startswith(("-", "+"))), "origin")
            url = _quiet(workspace, ["remote", "get-url", remote])
            url = re.sub(r"(https?://)[^/@\s]+@", r"\1", url)
            branch = _quiet(workspace, ["rev-parse", "--abbrev-ref", "HEAD"])
            target = " ".join(
                part for part in (url or remote, f"branch {branch}" if branch else "") if part
            )
            sections.append(f"Publishes to: {''.join(c for c in target if c.isprintable())}")
            commits = _quiet(workspace, ["log", "@{u}..HEAD", "--format=%h %s"])
            if commits:
                sections.append("Commits not yet on the remote:")
                sections += [f"  {line}" for line in _printable_lines(commits)]
    return "\n".join(sections)
