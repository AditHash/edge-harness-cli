import json
from pathlib import Path

import pytest

from code_harness.mcp_integration import MCPServerConfig
from code_harness.mcp_registry import (
    add_server,
    describe,
    load_servers,
    registry_path,
    remove_server,
)
from code_harness.mcp_remote import RemoteMCPConfig
from code_harness.workspace import WorkspaceError


def _file(tmp_path: Path, name: str, data: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_add_list_remove_local_and_remote(tmp_path: Path) -> None:
    state = tmp_path / "state"
    local = _file(tmp_path, "l.json", {"name": "notes", "command": ["python3", "s.py"]})
    remote = _file(tmp_path, "r.json", {"name": "docs", "url": "https://example.com/mcp"})
    assert add_server(state, local) == MCPServerConfig("notes", ("python3", "s.py"))
    assert add_server(state, remote) == RemoteMCPConfig("docs", "https://example.com/mcp")
    servers = load_servers(state)
    assert set(servers) == {"notes", "docs"}
    assert "python3 s.py" in describe(servers["notes"])
    assert "https://example.com/mcp" in describe(servers["docs"])
    assert remove_server(state, "notes") and not remove_server(state, "notes")
    assert set(load_servers(state)) == {"docs"}


def test_same_name_replaces(tmp_path: Path) -> None:
    state = tmp_path / "state"
    add_server(state, _file(tmp_path, "a.json", {"name": "x", "command": ["one"]}))
    add_server(state, _file(tmp_path, "b.json", {"name": "x", "command": ["two"]}))
    assert load_servers(state)["x"] == MCPServerConfig("x", ("two",))


@pytest.mark.parametrize(
    "data",
    [
        {"name": "x", "command": ["a"], "env": {"TOKEN": "s"}},
        {"name": "x", "url": "https://e.com", "headers": {"a": "b"}},
        {"name": "bad name", "command": ["a"]},
        {"name": "x", "command": []},
    ],
)
def test_credentials_and_bad_definitions_are_refused(tmp_path: Path, data: dict) -> None:
    state = tmp_path / "state"
    with pytest.raises(WorkspaceError):
        add_server(state, _file(tmp_path, "c.json", data))
    assert not registry_path(state).exists()


def test_tampered_registry_entries_are_ignored(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    registry_path(state).write_text(
        json.dumps(
            [
                {"name": "ok", "command": ["a"]},
                {"name": "evil", "command": ["a"], "env": {"X": "1"}},
                "junk",
            ]
        )
    )
    assert set(load_servers(state)) == {"ok"}
    registry_path(state).write_text("{not json")
    assert load_servers(state) == {}
