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


def test_saved_servers_connect_only_after_approval_and_name_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib

    from code_harness import cli
    from code_harness.config import Settings
    from code_harness.mcp_registry import add_server

    root = tmp_path / "repo"
    root.mkdir()
    state = tmp_path / "state"
    settings = Settings(workspace=root, harness_state_dir=state)
    add_server(state, _file(tmp_path, "a.json", {"name": "a", "command": ["one"]}))
    add_server(state, _file(tmp_path, "b.json", {"name": "b", "url": "https://example.com/m"}))
    connected_to: list[str] = []

    def fake_connect(stack, server, settings):
        connected_to.append(server.name)
        return [f"tool-{server.name}"]

    monkeypatch.setattr(cli, "_connect_server", fake_connect)
    names: list[str] = []
    with contextlib.ExitStack() as stack:
        tools = cli._open_mcp(stack, None, settings, ["a", "b"], names)
        assert tools == ["tool-a", "tool-b"] and names == ["a", "b"]
        with pytest.raises(WorkspaceError, match="No saved MCP server"):
            cli._open_mcp(stack, None, settings, ["missing"])
        with pytest.raises(WorkspaceError, match="twice"):
            cli._open_mcp(stack, None, settings, ["a", "a"])
        assert cli._open_mcp(stack, None, settings) == []
    assert connected_to == ["a", "b"]


def test_rejected_startup_does_not_connect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import contextlib

    from code_harness import cli, mcp_integration, mcp_remote
    from code_harness.config import Settings

    root = tmp_path / "repo"
    root.mkdir()
    settings = Settings(workspace=root, harness_state_dir=tmp_path / "state")
    monkeypatch.setattr(
        cli,
        "_terminal_io",
        lambda: cli.ReviewIO(True, lambda text: None, lambda prompt: "no"),
    )
    monkeypatch.setattr(
        mcp_integration, "connect_sandboxed_mcp", lambda *a, **k: pytest.fail("connected")
    )
    monkeypatch.setattr(mcp_remote, "connect_remote_mcp", lambda *a, **k: pytest.fail("connected"))
    monkeypatch.setattr(mcp_remote, "validate_remote_url", lambda url: None)
    with contextlib.ExitStack() as stack, pytest.raises(WorkspaceError, match="not approved"):
        cli._connect_server(stack, MCPServerConfig("a", ("one",)), settings)
    with contextlib.ExitStack() as stack, pytest.raises(WorkspaceError, match="not approved"):
        cli._connect_server(stack, RemoteMCPConfig("b", "https://example.com/m"), settings)


def test_launcher_manages_saved_servers_without_aws(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from code_harness import launcher

    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: pytest.fail("aws"))
    runner = CliRunner()
    config = _file(tmp_path, "n.json", {"name": "notes", "command": ["python3", "n.py"]})
    added = runner.invoke(launcher.launcher, ["--mcp-add", str(config)])
    assert added.exit_code == 0 and "Saved MCP server notes" in added.output
    assert "notes" in runner.invoke(launcher.launcher, ["--mcp-list"]).output
    removed = runner.invoke(launcher.launcher, ["--mcp-remove", "notes"])
    assert removed.exit_code == 0 and "Removed" in removed.output
    assert runner.invoke(launcher.launcher, ["--mcp-remove", "notes"]).exit_code == 1
    assert "No saved MCP servers" in runner.invoke(launcher.launcher, ["--mcp-list"]).output


def test_chat_mcp_command_lists_and_connect_rebuilds_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from code_harness import cli
    from code_harness.mcp_registry import add_server

    root = tmp_path / "repo"
    root.mkdir()
    state = tmp_path / "state"
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    add_server(state, _file(tmp_path, "a.json", {"name": "notes", "command": ["one"]}))
    built: list[list[str]] = []

    def make_agent(workspace, session_manager=None, guidance=None, model_id=None, mcp_tools=None):
        built.append(list(mcp_tools or []))
        return object()

    monkeypatch.setattr(cli, "_make_agent", make_agent)
    monkeypatch.setattr(cli, "_connect_server", lambda stack, server, settings: ["tool"])
    result = CliRunner().invoke(
        cli.app,
        ["chat", "--workspace", str(root)],
        input="/mcp\n/mcp connect missing\n/mcp connect notes\n/mcp connect notes\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "MCP servers" in result.output and "No saved MCP server named missing" in result.output
    assert "already connected" in result.output
    assert built == [[], ["tool"]]
