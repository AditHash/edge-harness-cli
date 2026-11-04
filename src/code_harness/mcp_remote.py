"""Opt-in remote MCP over streamable HTTP: HTTPS only, no credentials, per-call approval.

Remote servers run on someone else's machine, so nothing here executes locally. Tool arguments
leave the machine, which is why startup and every call need separate user approval (the same
``MCPApprovalGuard`` hook used for sandboxed local servers).
"""

import contextlib
import ipaddress
import json
import re
import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

from code_harness.workspace import WorkspaceError


@dataclass(frozen=True)
class RemoteMCPConfig:
    name: str
    url: str


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def _resolve(host: str, port: int) -> list[str]:
    return [str(item[4][0]) for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def validate_remote_url(
    url: str, resolver: Callable[[str, int], list[str]] = _resolve, *, allow_private: bool = False
) -> None:
    """Reject non-HTTPS, credentialed, or private/link-local/metadata destinations."""
    parts = urlsplit(url)
    if len(url) > 2048 or any(ord(ch) < 33 for ch in url):
        raise WorkspaceError("Remote MCP URL is invalid")
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise WorkspaceError("Remote MCP requires an https URL")
    if parts.username or parts.password or not parts.hostname or parts.fragment:
        raise WorkspaceError("Remote MCP URL must not contain credentials or fragments")
    if parts.query:
        raise WorkspaceError("Remote MCP URL must not carry a query string (possible token)")
    if allow_private:
        return
    try:
        addresses = resolver(parts.hostname, parts.port or 443)
    except OSError:
        raise WorkspaceError("Remote MCP host cannot be resolved") from None
    if not addresses or not all(_is_public(address) for address in addresses):
        raise WorkspaceError("Remote MCP host resolves to a non-public address")


def load_remote_config(path: Path) -> RemoteMCPConfig:
    try:
        if path.stat().st_size > 16384:
            raise ValueError
        return parse_remote_config(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError, UnicodeError, OSError):
        raise WorkspaceError("Invalid remote MCP configuration; expected name and url") from None


def parse_remote_config(data: Any) -> RemoteMCPConfig:
    """Validate a remote server definition; only name and url are accepted."""
    try:
        if not isinstance(data, dict) or set(data) != {"name", "url"}:
            raise ValueError
        name, url = data["name"], data["url"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
            raise ValueError
        if not isinstance(url, str):
            raise TypeError
    except (ValueError, TypeError, UnicodeError, OSError):
        raise WorkspaceError("Invalid remote MCP configuration; expected name and url") from None
    return RemoteMCPConfig(name, url)


@contextlib.contextmanager
def connect_remote_mcp(
    config: RemoteMCPConfig, *, allow_private: bool = False
) -> Iterator[tuple[list[MCPAgentTool], int]]:
    """Connect to one reviewed remote server; the second value mirrors the local API."""
    validate_remote_url(config.url, allow_private=allow_private)

    def transport() -> Any:
        # No headers, auth or cookies; redirects stay within the endpoint's origin.
        return streamable_http_client(config.url, http_client=create_mcp_http_client())

    client = MCPClient(transport, prefix=config.name, startup_timeout=30)
    entered = False
    try:
        client.__enter__()
        entered = True
        page = client.list_tools_sync()
        if page.pagination_token or len(page) > 50:
            raise WorkspaceError("MCP server exposes more than the allowed 50 tools")
        specs = [item.tool_spec for item in page]
        if len(json.dumps(specs, ensure_ascii=True)) > 65536:
            raise WorkspaceError("MCP tool definitions exceed the size limit")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", spec["name"]) for spec in specs):
            raise WorkspaceError("MCP server supplied an invalid tool name")
    except WorkspaceError:
        if entered:
            client.__exit__(None, None, None)
        raise
    except Exception:  # noqa: BLE001 - SDK errors can include server-supplied content
        if entered:
            with contextlib.suppress(Exception):
                client.__exit__(None, None, None)
        raise WorkspaceError("Remote MCP server failed or disconnected") from None
    try:
        yield list(page), 0
    finally:
        client.__exit__(None, None, None)
