import asyncio
from pathlib import Path

import pytest

from code_harness.sessions import Sessions, SQLiteStorage
from code_harness.workspace import WorkspaceError


def test_sqlite_storage_and_workspace_scoped_sessions(tmp_path: Path) -> None:
    workspace = tmp_path / "one"
    other = tmp_path / "two"
    workspace.mkdir()
    other.mkdir()
    db = tmp_path / "state" / "sessions.sqlite"
    sessions = Sessions(db, workspace)
    session_id = sessions.create()
    assert sessions.list() == [session_id]
    Sessions(db, workspace).require(session_id)
    with pytest.raises(WorkspaceError):
        Sessions(db, other).require(session_id)
    with pytest.raises(WorkspaceError):
        sessions.require("../bad")

    async def exercise() -> None:
        storage = SQLiteStorage(db)
        await storage.write("test/a", b"hello")
        await storage.write("test/b", b"world")
        assert await storage.list("test/") == ["test/a", "test/b"]
        assert await storage.read("test/a") == b"hello"
        await storage.delete("test/a")
        assert await storage.read("test/a") is None

    asyncio.run(exercise())
