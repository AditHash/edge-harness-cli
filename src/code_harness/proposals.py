"""Immutable, reviewable file-change proposals; this module does not write files."""

import difflib
import hashlib
import uuid
from dataclasses import dataclass

from code_harness.workspace import MAX_FILE_BYTES, Workspace, WorkspaceError


@dataclass(frozen=True)
class Proposal:
    id: str
    path: str
    original_digest: str | None
    new_content: str | None
    diff: str


def propose(workspace: Workspace, path: str, new_content: str | None) -> Proposal:
    target = workspace.path(path)
    if new_content is not None and len(new_content.encode("utf-8")) > MAX_FILE_BYTES:
        raise WorkspaceError("Proposed file exceeds size limit")
    if target.exists():
        original = workspace.read(path)
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    else:
        if new_content is None:
            raise WorkspaceError("Cannot delete a missing file")
        original = ""
        digest = None
    if digest is not None and new_content == original:
        raise WorkspaceError("Proposal makes no changes")
    old_name = f"a/{path}" if digest is not None else "/dev/null"
    new_name = f"b/{path}" if new_content is not None else "/dev/null"
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            (new_content or "").splitlines(keepends=True),
            fromfile=old_name,
            tofile=new_name,
        )
    )
    # Unified diffs do not encode an empty file creation/deletion. State that explicitly.
    if not diff:
        diff = f"{old_name} -> {new_name} (empty file)\n"
    return Proposal(uuid.uuid4().hex, path, digest, new_content, diff)
