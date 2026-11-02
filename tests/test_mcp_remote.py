import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.mcp_remote import (
    RemoteMCPConfig,
    connect_remote_mcp,
    load_remote_config,
    validate_remote_url,
)
from code_harness.streaming import stream_turn
from code_harness.workspace import WorkspaceError

FIXTURE = Path(__file__).parent / "fixtures" / "remote_mcp_server.py"
PUBLIC = "93.184.216.34"


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "https://user:pw@example.com/mcp",
        "https://example.com/mcp?token=abc",
        "https://example.com/mcp#frag",
        "https://",
    ],
)
def test_unsafe_urls_rejected(url: str) -> None:
    with pytest.raises(WorkspaceError):
        validate_remote_url(url, resolver=lambda h, p: [PUBLIC])


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "192.168.1.2"]
)
def test_private_destinations_rejected(address: str) -> None:
    url = "https://example.com/mcp"
    with pytest.raises(WorkspaceError):
        validate_remote_url(url, resolver=lambda h, p: [address])
    with pytest.raises(WorkspaceError):
        validate_remote_url(url, resolver=lambda h, p: [PUBLIC, address])
    validate_remote_url(url, resolver=lambda h, p: [PUBLIC])


def test_remote_config_has_no_credential_fields(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text('{"name": "r", "url": "https://example.com/mcp", "headers": {}}')
    with pytest.raises(WorkspaceError):
        load_remote_config(path)
    path.write_text('{"name": "r", "url": "https://example.com/mcp"}')
    assert load_remote_config(path) == RemoteMCPConfig("r", "https://example.com/mcp")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.parametrize("approve", [False, True])
def test_remote_tool_call_needs_approval_and_reaches_server_only_if_approved(
    tmp_path: Path, approve: bool
) -> None:
    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, str(FIXTURE), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.1)
        config = RemoteMCPConfig("remote", f"http://127.0.0.1:{port}/mcp")
        requests: list[str] = []
        reviews: list[tuple[str, str]] = []
        calls = 0
        with connect_remote_mcp(config, allow_private=True) as (tools, _):
            tool_name = tools[0].tool_spec["name"]

            async def stream(messages, *args, **kwargs):
                nonlocal calls
                calls += 1
                requests.append(str(messages))
                yield {"messageStart": {"role": "assistant"}}
                if calls == 1:
                    start = {"toolUseId": "r1", "name": tool_name}
                    yield {"contentBlockStart": {"start": {"toolUse": start}}}
                    yield {"contentBlockDelta": {"delta": {"toolUse": {"input": '{"text": "hi"}'}}}}
                    yield {"contentBlockStop": {}}
                    yield {"messageStop": {"stopReason": "tool_use"}}
                else:
                    yield {"contentBlockDelta": {"delta": {"text": "done"}}}
                    yield {"contentBlockStop": {}}
                    yield {"messageStop": {"stopReason": "end_turn"}}
                usage = {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}
                yield {"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}}

            model = MagicMock(spec=Model)
            model.stateful = False
            model.get_config.return_value = {}
            model.estimate_utilization.return_value = 0.0
            model.stream.side_effect = stream

            def review(name: str, payload: str) -> bool:
                reviews.append((name, payload))
                return approve

            agent = create_agent(
                Settings(workspace=tmp_path), model=model, mcp_tools=tools, mcp_review=review
            )

            async def run() -> None:
                async for _ in stream_turn(agent, "probe"):
                    pass

            asyncio.run(run())
        assert len(reviews) == 1 and '"text": "hi"' in reviews[0][1]
        if approve:
            assert "remote-ok:hi" in requests[-1]
        else:
            assert "remote-ok" not in requests[-1] and "rejected" in requests[-1]
    finally:
        server.kill()
        server.wait()
