# mypy: disable-error-code=attr-defined
"""Reviewed file changes on Linux and macOS, anchored to pinned directory descriptors.

Every path component is opened with ``O_NOFOLLOW | O_DIRECTORY`` relative to the previous
descriptor, so symlinks and renamed ancestors cannot redirect a write outside the workspace.
"""

import fcntl
import hashlib
import os
import stat
from pathlib import Path

from code_harness.proposals import Proposal
from code_harness.workspace import (
    MAX_FILE_BYTES,
    Workspace,
    WorkspaceError,
    _protected,
)

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_parent(workspace: Workspace, relative: str) -> tuple[int, str]:
    """Open the target's parent by walking from the workspace root without following links."""
    parts = Path(relative).parts
    if not parts or _protected(parts):
        raise WorkspaceError("Path is protected")
    try:
        current = os.open(workspace.root, _DIR_FLAGS)
    except OSError:
        raise WorkspaceError("Cannot open the workspace directory") from None
    try:
        for part in parts[:-1]:
            try:
                following = os.open(part, _DIR_FLAGS, dir_fd=current)
            except OSError:
                raise WorkspaceError(
                    "Parent directory is missing, linked or inaccessible"
                ) from None
            os.close(current)
            current = following
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Write made no progress")
        view = view[written:]


def _check_regular(fd: int) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WorkspaceError("Target is not a regular, unlinked file")
    return info


def _read_original(fd: int, proposal: Proposal) -> bytes:
    data = b""
    while len(data) <= MAX_FILE_BYTES:
        chunk = os.read(fd, MAX_FILE_BYTES + 1 - len(data))
        if not chunk:
            break
        data += chunk
    if len(data) > MAX_FILE_BYTES or hashlib.sha256(data).hexdigest() != proposal.original_digest:
        raise WorkspaceError("File changed after review; no change was applied")
    return data


def apply_posix(workspace: Workspace, proposal: Proposal, journal) -> str:
    """Apply an already reviewed proposal. ``journal(root, path, original)`` returns a Path."""
    parent, name = _open_parent(workspace, proposal.path)
    try:
        if proposal.original_digest is None:
            return _create(workspace, proposal, parent, name, journal)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(name, flags, dir_fd=parent)
        except OSError:
            raise WorkspaceError("Cannot open the reviewed file") from None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = _check_regular(fd)
            original = _read_original(fd, proposal)
            recovery = journal(workspace.root, proposal.path, original)
            if proposal.new_content is None:
                return _delete(proposal, parent, name, fd, info, recovery)
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                _write_all(fd, proposal.new_content.encode("utf-8"))
                os.ftruncate(fd, len(proposal.new_content.encode("utf-8")))
                os.fsync(fd)
            except BaseException as error:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _write_all(fd, original)
                    os.ftruncate(fd, len(original))
                    os.fsync(fd)
                except OSError:
                    raise WorkspaceError(
                        f"Edit failed; original bytes are saved in {recovery}"
                    ) from error
                recovery.unlink(missing_ok=True)
                raise WorkspaceError("Edit failed and the original file was restored") from error
            recovery.unlink(missing_ok=True)
            return f"Applied approved change to {proposal.path}."
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _delete(proposal: Proposal, parent: int, name: str, fd: int, info, recovery: Path) -> str:
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise WorkspaceError("File was replaced after review; no change was applied")
        os.unlink(name, dir_fd=parent)
    except OSError as error:
        recovery.unlink(missing_ok=True)
        raise WorkspaceError("Deletion failed; no change was applied") from error
    recovery.unlink(missing_ok=True)
    return f"Deleted approved file {proposal.path}."


def _create(workspace: Workspace, proposal: Proposal, parent: int, name: str, journal) -> str:
    assert proposal.new_content is not None
    recovery = journal(workspace.root, proposal.path, None)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, 0o644, dir_fd=parent)
    except OSError:
        recovery.unlink(missing_ok=True)
        raise WorkspaceError("Cannot create the reviewed file (it may already exist)") from None
    try:
        _write_all(fd, proposal.new_content.encode("utf-8"))
        os.fsync(fd)
    except BaseException as error:
        os.close(fd)
        fd = -1
        try:
            os.unlink(name, dir_fd=parent)
        except OSError:
            raise WorkspaceError(
                f"Creation failed; recovery record is saved in {recovery}"
            ) from error
        recovery.unlink(missing_ok=True)
        raise WorkspaceError("Creation failed and the new file was removed") from error
    finally:
        if fd >= 0:
            os.close(fd)
    recovery.unlink(missing_ok=True)
    return f"Created approved file {proposal.path}."
