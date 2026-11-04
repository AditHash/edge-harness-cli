"""Saved MCP server definitions, kept only in the user's state directory.

Repository files can never add a server here: a definition is saved only when the user names a
file with `--mcp-add`, and connecting still needs approval every time. Entries hold the same
validated fields as a config file (name plus command, or name plus url), never credentials.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from code_harness.mcp_integration import MCPServerConfig, parse_mcp_config
from code_harness.mcp_remote import RemoteMCPConfig, parse_remote_config
from code_harness.workspace import WorkspaceError

MAX_CONFIG_BYTES = 16384
AnyServer = MCPServerConfig | RemoteMCPConfig


def registry_path(state_dir: Path) -> Path:
    return state_dir / "mcp-servers.json"


def parse_server(data: Any) -> AnyServer:
    if isinstance(data, dict) and "url" in data:
        return parse_remote_config(data)
    return parse_mcp_config(data)


def read_server_file(path: Path) -> AnyServer:
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise WorkspaceError("MCP configuration exceeds the size limit")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise WorkspaceError("Invalid MCP configuration file") from None
    return parse_server(data)


def load_servers(state_dir: Path) -> dict[str, AnyServer]:
    """Saved servers by name; a malformed file or entry is ignored, not trusted."""
    try:
        data = json.loads(registry_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    servers: dict[str, AnyServer] = {}
    for entry in data if isinstance(data, list) else []:
        try:
            server = parse_server(entry)
        except WorkspaceError:
            continue
        servers[server.name] = server
    return servers


def _serialize(server: AnyServer) -> dict[str, Any]:
    if isinstance(server, RemoteMCPConfig):
        return {"name": server.name, "url": server.url}
    return {"name": server.name, "command": list(server.command)}


def _save(state_dir: Path, servers: dict[str, AnyServer]) -> None:
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="mcp-", suffix=".json", dir=state_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump([_serialize(item) for item in servers.values()], stream, indent=2)
        os.replace(name, registry_path(state_dir))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def add_server(state_dir: Path, file: Path) -> AnyServer:
    server = read_server_file(file)
    servers = load_servers(state_dir)
    servers[server.name] = server
    _save(state_dir, servers)
    return server


def remove_server(state_dir: Path, name: str) -> bool:
    servers = load_servers(state_dir)
    if name not in servers:
        return False
    del servers[name]
    _save(state_dir, servers)
    return True


def describe(server: AnyServer) -> str:
    if isinstance(server, RemoteMCPConfig):
        return f"remote  {server.url}"
    return "local   " + " ".join(server.command)
