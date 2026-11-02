import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.streaming import stream_turn


def test_only_guarded_tools_registered(tmp_path: Path) -> None:
    model = MagicMock(spec=Model)
    model.stateful = False
    model.get_config.return_value = {}
    agent = create_agent(Settings(workspace=tmp_path), model=model)
    assert set(agent.tool_registry.registry) == {"list_repo", "read_code", "search_code"}


def test_explicit_model_and_region_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    import pytest

    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        create_agent(Settings(workspace=tmp_path))


def test_real_agent_streams_fake_model_without_cloud(tmp_path: Path) -> None:
    async def model_stream(*args, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": "Hello from fixture"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
                "metrics": {"latencyMs": 1},
            }
        }

    model = MagicMock(spec=Model)
    model.stateful = False
    model.get_config.return_value = {}
    model.stream.side_effect = model_stream
    agent = create_agent(Settings(workspace=tmp_path), model=model)

    async def collect():
        return [event async for event in stream_turn(agent, "hello")]

    events = asyncio.run(collect())
    assert "".join(event.text for event in events) == "Hello from fixture"
    assert events[-1].kind == "turn_completed"


def test_strands_invokes_langchain_search_without_exposing_env(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def useful_function():\n    pass\n")
    (tmp_path / ".env").write_text("useful_function SECRET")
    observed = []

    async def model_stream(messages, *args, **kwargs):
        observed.append(messages.copy())
        yield {"messageStart": {"role": "assistant"}}
        if len(observed) == 1:
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": "call1", "name": "search_code"}}
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": '{"query":"useful_function"}'}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "Found app.py"}}}
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
    model.stream.side_effect = model_stream
    agent = create_agent(Settings(workspace=tmp_path), model=model)

    async def run():
        return [event async for event in stream_turn(agent, "find the function")]

    asyncio.run(run())
    assert len(observed) == 2
    assert "app.py:1" in str(observed[-1])
    assert "SECRET" not in str(observed[-1])
