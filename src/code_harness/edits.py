"""Reviewed in-place edits using exclusive file and directory handles on Windows."""

import base64
import ctypes
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from code_harness.approvals import validate_reviewable
from code_harness.proposals import Proposal, propose
from code_harness.workspace import (
    MAX_FILE_BYTES,
    Workspace,
    WorkspaceError,
    _handle_path,
    _protected,
)


@contextmanager
def _locked_directories(directory: Path) -> Iterator[None]:
    if os.name != "nt":
        raise WorkspaceError("Secure edits currently require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    get_path = kernel.GetFinalPathNameByHandleW
    get_path.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    get_path.restype = ctypes.c_uint32

    class AttributeTagInfo(ctypes.Structure):
        _fields_ = [("attributes", ctypes.c_uint32), ("tag", ctypes.c_uint32)]

    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    get_info.restype = ctypes.c_int
    handles = []
    try:
        for expected in reversed([directory, *directory.parents]):
            # FILE_LIST_DIRECTORY participates in share checks; attribute-only access does not.
            # Denying FILE_SHARE_DELETE prevents rename or replacement while the handle is open.
            handle = create(str(expected), 0x1, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise WorkspaceError("Cannot secure an ancestor directory against renaming")
            handles.append(handle)
            info = AttributeTagInfo()
            if not get_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise WorkspaceError("Cannot inspect an ancestor directory")
            if info.attributes & 0x400 or not info.attributes & 0x10:
                raise WorkspaceError("Ancestor directory is a link or reparse point")
            buffer = ctypes.create_unicode_buffer(32768)
            size = get_path(handle, buffer, len(buffer), 0)
            if not size or size >= len(buffer):
                raise WorkspaceError("Cannot verify an ancestor directory handle")
            actual = buffer.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if Path(actual) != expected:
                raise WorkspaceError("Ancestor directory changed while it was opened")
        yield
    finally:
        for handle in reversed(handles):
            close(handle)


def _open_exclusive(path: Path, *, create_new: bool = False, allow_delete: bool = False) -> int:
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create = kernel.CreateFileW
    create.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create.restype = ctypes.c_void_p
    access = 0x80000000 | 0x40000000 | (0x00010000 if create_new or allow_delete else 0)
    handle = create(str(path), access, 0, None, 1 if create_new else 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise WorkspaceError("Cannot obtain exclusive access to the target file")
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        raise


def _write_all(stream, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = stream.write(view)
        if written is None or written <= 0:
            raise OSError("Write made no progress")
        view = view[written:]


def _journal(root: Path, relative: str, original: bytes | None) -> Path:
    directory = Path.home() / ".edge-harness" / "recovery"
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="edit-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "workspace": str(root),
                    "path": relative,
                    "original_base64": (
                        base64.b64encode(original).decode("ascii") if original is not None else None
                    ),
                },
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _delete_open_file(fd: int) -> None:
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    set_info = kernel.SetFileInformationByHandle
    set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    set_info.restype = ctypes.c_int
    disposition = ctypes.c_ubyte(1)
    if not set_info(
        msvcrt.get_osfhandle(fd), 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
    ):
        raise OSError(ctypes.get_last_error(), "Cannot remove file through its handle")  # type: ignore[attr-defined]


def _create_reviewed_file(workspace: Workspace, proposal: Proposal, target: Path) -> str:
    assert proposal.new_content is not None
    with _locked_directories(target.parent):
        recovery = _journal(workspace.root, proposal.path, None)
        try:
            fd = _open_exclusive(target, create_new=True)
        except BaseException:
            recovery.unlink(missing_ok=True)
            raise
        with os.fdopen(fd, "r+b", buffering=0) as stream:
            try:
                info = os.fstat(stream.fileno())
                actual = _handle_path(stream.fileno())
                if actual != target or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise WorkspaceError("Created file does not match the reviewed target")
                _write_all(stream, proposal.new_content.encode("utf-8"))
                stream.truncate()
                os.fsync(stream.fileno())
            except BaseException as error:
                try:
                    _delete_open_file(stream.fileno())
                except OSError:
                    raise WorkspaceError(
                        f"Creation failed; recovery record is saved in {recovery}"
                    ) from error
                recovery.unlink(missing_ok=True)
                raise WorkspaceError("Creation failed and the new file was removed") from error
        recovery.unlink(missing_ok=True)
    return f"Created approved file {proposal.path}."


def _validate_opened_file(workspace: Workspace, target: Path, stream) -> None:
    info = os.fstat(stream.fileno())
    actual = _handle_path(stream.fileno())
    try:
        actual_parts = actual.relative_to(workspace.root).parts
    except ValueError:
        raise WorkspaceError("Opened file is outside the workspace") from None
    reparse = getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    )
    if (
        actual != target
        or _protected(actual_parts)
        or reparse
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        raise WorkspaceError("Opened file is protected or is not the reviewed regular file")


def _read_reviewed_original(stream, proposal: Proposal) -> bytes:
    original = stream.read(MAX_FILE_BYTES + 1)
    if (
        len(original) > MAX_FILE_BYTES
        or hashlib.sha256(original).hexdigest() != proposal.original_digest
    ):
        raise WorkspaceError("File changed after review; no change was applied")
    return original


def _delete_reviewed_file(workspace: Workspace, proposal: Proposal, target: Path) -> str:
    with _locked_directories(target.parent):
        with os.fdopen(_open_exclusive(target, allow_delete=True), "r+b", buffering=0) as stream:
            _validate_opened_file(workspace, target, stream)
            original = _read_reviewed_original(stream, proposal)
            recovery = _journal(workspace.root, proposal.path, original)
            try:
                _delete_open_file(stream.fileno())
            except OSError as error:
                recovery.unlink(missing_ok=True)
                raise WorkspaceError("Deletion failed; no change was applied") from error
        recovery.unlink(missing_ok=True)
    return f"Deleted approved file {proposal.path}."


def apply_reviewed_edit(
    workspace: Workspace, proposal: Proposal, review: Callable[[Proposal], bool]
) -> str:
    """Review and apply one file operation, checking authorization at the handle."""
    if os.name != "nt":
        raise WorkspaceError("Secure edits currently require Windows")
    fresh = propose(workspace, proposal.path, proposal.new_content)
    if fresh.original_digest != proposal.original_digest or fresh.diff != proposal.diff:
        raise WorkspaceError("Proposal is stale or does not match its diff")
    validate_reviewable(proposal)
    if not review(proposal):
        return "Change rejected; no file was modified."
    target = workspace.path(proposal.path)
    if proposal.new_content is None:
        return _delete_reviewed_file(workspace, proposal, target)
    if proposal.original_digest is None:
        return _create_reviewed_file(workspace, proposal, target)
    with (
        _locked_directories(target.parent),
        os.fdopen(_open_exclusive(target), "r+b", buffering=0) as stream,
    ):
        _validate_opened_file(workspace, target, stream)
        original = _read_reviewed_original(stream, proposal)
        recovery = _journal(workspace.root, proposal.path, original)
        try:
            stream.seek(0)
            _write_all(stream, proposal.new_content.encode("utf-8"))
            stream.truncate()
            os.fsync(stream.fileno())
        except BaseException as error:
            try:
                stream.seek(0)
                _write_all(stream, original)
                stream.truncate()
                os.fsync(stream.fileno())
            except OSError:
                raise WorkspaceError(
                    f"Edit failed; original bytes are saved in {recovery}"
                ) from error
            recovery.unlink(missing_ok=True)
            raise WorkspaceError("Edit failed and the original file was restored") from error
        recovery.unlink(missing_ok=True)
    return f"Applied approved change to {proposal.path}."
