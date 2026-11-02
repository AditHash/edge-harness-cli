"""Opt-in, isolated stdio MCP servers with independent per-call approval."""

import contextlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.client.stdio import StdioServerParameters, stdio_client
from strands.hooks.events import BeforeToolCallEvent
from strands.hooks.registry import HookProvider, HookRegistry
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

from code_harness.platforms import (
    helper_command,
    sandbox_backend,
    sandbox_unavailable_reason,
    wsl_executable,
)
from code_harness.sandbox import _snapshot
from code_harness.workspace import Workspace, WorkspaceError, _protected


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    command: tuple[str, ...]


def load_mcp_config(path: Path) -> MCPServerConfig:
    """Load a user-selected local server definition; no credentials are accepted."""
    try:
        if path.stat().st_size > 16384:
            raise WorkspaceError("MCP configuration exceeds the size limit")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) != {"name", "command"}:
            raise ValueError
        name = data["name"]
        command = data["command"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
            raise ValueError
        if not isinstance(command, list) or not 1 <= len(command) <= 16:
            raise ValueError
        if any(
            not isinstance(arg, str) or not arg or len(arg) > 1024 or any(ord(c) < 32 for c in arg)
            for arg in command
        ):
            raise ValueError
        return MCPServerConfig(name, tuple(command))
    except (ValueError, TypeError, UnicodeError, OSError):
        raise WorkspaceError("Invalid MCP configuration; expected name and command array") from None


class MCPApprovalGuard(HookProvider):
    """A non-bypassable agent hook for every registered MCP tool call."""

    def __init__(self, review: Callable[[str, str], bool]):
        self.review = review

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)

    def _before_tool(self, event: BeforeToolCallEvent) -> None:
        if not isinstance(event.selected_tool, MCPAgentTool):
            return
        name = event.tool_use.get("name", "")
        try:
            payload = json.dumps(event.tool_use.get("input", {}), ensure_ascii=True, sort_keys=True)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or len(payload) > 8192:
                event.cancel_tool = "MCP tool name or arguments exceed policy limits"
                return
            if not self.review(name, payload):
                event.cancel_tool = "MCP tool call rejected by the user"
        except (EOFError, KeyboardInterrupt, OSError, ValueError, TypeError):
            event.cancel_tool = "MCP tool call was not approved"


def _wsl_path(snapshot: Path, distro: str) -> str:
    try:
        conversion = subprocess.run(
            [
                wsl_executable(),
                "-d",
                distro,
                "--exec",
                "/usr/bin/wslpath",
                "-a",
                "-u",
                str(snapshot),
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
        linux_path = conversion.stdout.decode("utf-8").strip()
        if not linux_path.startswith("/") or "\n" in linux_path:
            raise ValueError
        return linux_path
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError):
        raise WorkspaceError("Cannot prepare an isolated MCP workspace in WSL") from None


@contextlib.contextmanager
def connect_sandboxed_mcp(
    workspace: Workspace,
    config: MCPServerConfig,
    state_dir: Path,
    *,
    distro: str = "Ubuntu",
) -> Iterator[tuple[list[MCPAgentTool], int]]:
    """Start one local stdio server in a no-network, copied WSL workspace."""
    backend = sandbox_backend()
    if backend is None:
        raise WorkspaceError(f"No sandbox available: {sandbox_unavailable_reason()}")
    archive, omitted = _snapshot(workspace)
    try:
        relative = state_dir.resolve().relative_to(workspace.root)
    except ValueError:
        pass
    else:
        if not _protected(relative.parts):
            raise WorkspaceError("MCP state inside the workspace must be in a protected directory")
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="mcp-snapshot-", suffix=".tar", dir=state_dir)
    snapshot = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(archive)
        linux_path = str(snapshot)
        if backend == "wsl":
            linux_path = _wsl_path(snapshot, distro)
        helper = Path(__file__).with_name("wsl_mcp_helper.py").read_text(encoding="utf-8")
        argv = helper_command(helper, distro, linux_path, json.dumps(config.command))
        params = StdioServerParameters(command=argv[0], args=argv[1:])
        with open(os.devnull, "w", encoding="utf-8") as errors:
            transport: Any = lambda: stdio_client(params, errlog=errors)
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
                raise WorkspaceError("Isolated MCP server failed or disconnected") from None
            try:
                yield list(page), omitted
            finally:
                client.__exit__(None, None, None)
    finally:
        snapshot.unlink(missing_ok=True)
