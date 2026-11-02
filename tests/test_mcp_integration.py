import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.mcp_integration import MCPServerConfig, connect_sandboxed_mcp, load_mcp_config
from code_harness.streaming import stream_turn
from code_harness.workspace import Workspace, WorkspaceError


def test_mcp_config_is_explicit_and_rejects_extra_fields(tmp_path: Path) -> None:
    config = tmp_path / "server.json"
    config.write_text(
        json.dumps({"name": "fixture", "command": ["python3", "/workspace/server.py"]})
    )
    assert load_mcp_config(config) == MCPServerConfig(
        "fixture", ("python3", "/workspace/server.py")
    )
    config.write_text(json.dumps({"name": "fixture", "command": ["python3"], "env": {}}))
    with pytest.raises(WorkspaceError):
        load_mcp_config(config)


@pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_WSL") != "1", reason="WSL sandbox integration is opt-in"
)
def test_isolated_mcp_server_discovery(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "mcp_fixture_server.py"
    (root / "server.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".env").write_text("secret")
    config = MCPServerConfig("fixture", ("python3", "/workspace/server.py"))
    with connect_sandboxed_mcp(Workspace(root), config, tmp_path / "state") as (tools, omitted):
        assert omitted == 0
        assert len(tools) == 1
        assert "probe" in tools[0].tool_spec["name"]


@pytest.mark.skipif(
    os.environ.get("HARNESS_TEST_WSL") != "1", reason="WSL sandbox integration is opt-in"
)
@pytest.mark.parametrize("approve", [False, True])
def test_mcp_agent_tool_requires_approval_and_is_isolated(tmp_path: Path, approve: bool) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "mcp_fixture_server.py"
    (root / "server.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    (root / ".env").write_text("secret")
    config = MCPServerConfig("fixture", ("python3", "/workspace/server.py"))
    requests = []
    reviews = []
    calls = 0

    with connect_sandboxed_mcp(Workspace(root), config, tmp_path / "state") as (tools, _omitted):
        tool_name = tools[0].tool_spec["name"]

        async def model_stream(messages, *args, **kwargs):
            nonlocal calls
            calls += 1
            requests.append([message.copy() for message in messages])
            yield {"messageStart": {"role": "assistant"}}
            if calls == 1:
                yield {
                    "contentBlockStart": {
                        "start": {"toolUse": {"toolUseId": "mcp1", "name": tool_name}}
                    }
                }
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            else:
                yield {"contentBlockDelta": {"delta": {"text": "done"}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "end_turn"}}
            yield {
                "metadata": {
                    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                    "metrics": {"latencyMs": 1},
                }
            }

        model = MagicMock(spec=Model)
        model.stateful = False
        model.get_config.return_value = {}
        model.estimate_utilization.return_value = 0.0
        model.stream.side_effect = model_stream

        def review(name, payload):
            reviews.append((name, payload))
            return approve

        agent = create_agent(
            Settings(workspace=root), model=model, mcp_tools=tools, mcp_review=review
        )

        async def run():
            return [event async for event in stream_turn(agent, "probe")]

        asyncio.run(run())
    assert len(reviews) == 1 and reviews[0][1] == "{}"
    assert calls == 2
    if approve:
        assert "isolated" in str(requests[-1]) and "unsafe" not in str(requests[-1])
    else:
        assert "rejected" in str(requests[-1]) and "isolated" not in str(requests[-1])
