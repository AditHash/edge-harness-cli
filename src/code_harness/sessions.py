"""SQLite storage for Strands snapshots and workspace-scoped session metadata."""

import asyncio
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from strands.hooks.events import AfterInvocationEvent
from strands.session import SnapshotSessionManager
from strands.storage.storage import Storage

from code_harness.workspace import WorkspaceError, _protected


class SQLiteStorage(Storage):
    """Strands Storage implementation; each write is a single SQLite transaction."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if path.is_symlink():
            raise WorkspaceError("Session database cannot be a symbolic link")
        if not path.exists():
            import os

            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        with sqlite3.connect(path, timeout=10) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS blobs (key TEXT PRIMARY KEY, value BLOB NOT NULL)"
            )

    def _write(self, key: str, data: bytes) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute(
                "INSERT INTO blobs (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, data),
            )

    async def write(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, key, data)

    def _read(self, key: str) -> bytes | None:
        with sqlite3.connect(self.path, timeout=10) as db:
            row = db.execute("SELECT value FROM blobs WHERE key = ?", (key,)).fetchone()
            return bytes(row[0]) if row else None

    async def read(self, key: str) -> bytes | None:
        return await asyncio.to_thread(self._read, key)

    def _delete(self, key: str) -> None:
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("DELETE FROM blobs WHERE key = ?", (key,))

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._delete, key)

    def _list(self, prefix: str) -> list[str]:
        with sqlite3.connect(self.path, timeout=10) as db:
            rows = db.execute(
                "SELECT key FROM blobs WHERE substr(key, 1, length(?)) = ? ORDER BY key",
                (prefix, prefix),
            ).fetchall()
            return [str(row[0]) for row in rows]

    async def list(self, query: str = "") -> list[str]:
        return await asyncio.to_thread(self._list, query)


class CompletedTurnSessionManager(SnapshotSessionManager):
    """Persist only finished turns, so restoring never resumes a pending tool call."""

    async def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        if event.result is not None and event.result.stop_reason in {"end_turn", "stop_sequence"}:
            await super()._on_after_invocation(event)


class Sessions:
    """Keep session selection bound to one canonical workspace."""

    def __init__(self, database: Path, workspace: Path):
        root = workspace.resolve()
        try:
            relative = database.resolve().relative_to(root)
        except ValueError:
            pass
        else:
            if not _protected(relative.parts):
                raise WorkspaceError(
                    "Session database inside the workspace must be in a protected directory"
                )
        self.storage = SQLiteStorage(database)
        self.workspace = str(root)

    def create(self) -> str:
        session_id = uuid.uuid4().hex
        self.storage._write(f"workspace/{session_id}", self.workspace.encode("utf-8"))
        return session_id

    def require(self, session_id: str) -> None:
        if len(session_id) != 32 or any(ch not in "0123456789abcdef" for ch in session_id):
            raise WorkspaceError("Invalid session ID")
        owner = self.storage._read(f"workspace/{session_id}")
        if owner != self.workspace.encode("utf-8"):
            raise WorkspaceError("Session does not exist in this workspace")

    def list(self) -> list[str]:
        return [
            key.removeprefix("workspace/")
            for key in self.storage._list("workspace/")
            if self.storage._read(key) == self.workspace.encode("utf-8")
        ]

    def manager(self, session_id: str) -> Any:
        self.require(session_id)
        return CompletedTurnSessionManager(
            session_id, storage=self.storage, save_latest_on="invocation"
        )
