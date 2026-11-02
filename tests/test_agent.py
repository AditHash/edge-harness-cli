import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from strands.models import Model

from code_harness.agent import create_agent
from code_harness.approvals import review_proposal
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
    assert (
        "".join(event.text for event in events if event.kind == "text_delta")
        == "Hello from fixture"
    )
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


def test_two_turns_preserve_conversation_with_real_strands_agent(tmp_path: Path) -> None:
    requests = []

    async def model_stream(messages, *args, **kwargs):
        requests.append([message.copy() for message in messages])
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "Acknowledged"}}}
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

    async def run(prompt: str) -> None:
        async for _ in stream_turn(agent, prompt):
            pass

    # The terminal uses a separate asyncio.run for each input.
    asyncio.run(run("First instruction"))
    asyncio.run(run("Follow-up"))
    assert len(requests) == 2
    assert "First instruction" in str(requests[1])
    assert "Acknowledged" in str(requests[1])
    assert "Follow-up" in str(requests[1])


@pytest.mark.skipif(os.name != "nt", reason="secure edits currently require Windows")
@pytest.mark.parametrize("answer, expected", [("yes", b"new\n"), ("no", b"old\n")])
def test_agent_edit_requires_exact_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, expected: bytes
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"old\n")
    shown = []
    calls = 0

    async def model_stream(messages, *args, **kwargs):
        nonlocal calls
        calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if calls == 1:
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": "edit1", "name": "edit_code"}}
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {
                        "toolUse": {"input": json.dumps({"path": "app.py", "new_content": "new\n"})}
                    }
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "Done"}}}
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

    def review(proposal):
        return review_proposal(
            proposal, interactive=True, display=shown.append, read_reply=lambda _: answer
        )

    agent = create_agent(Settings(workspace=root), model=model, review=review)

    async def run():
        return [event async for event in stream_turn(agent, "change the file")]

    asyncio.run(run())
    assert calls == 2
    assert len(shown) == 1 and "-old" in shown[0] and "+new" in shown[0]
    assert target.read_bytes() == expected


@pytest.mark.skipif(os.name != "nt", reason="secure edits currently require Windows")
def test_agent_deletion_requires_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "app.py"
    target.write_bytes(b"old\n")
    calls = 0
    reviewed = []

    async def model_stream(messages, *args, **kwargs):
        nonlocal calls
        calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if calls == 1:
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": "delete1", "name": "delete_code"}}
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {"toolUse": {"input": json.dumps({"path": "app.py"})}}
                }
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "Done"}}}
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

    def review(proposal):
        reviewed.append(proposal.diff)
        return True

    agent = create_agent(Settings(workspace=root), model=model, review=review)

    async def run():
        return [event async for event in stream_turn(agent, "delete the file")]

    asyncio.run(run())
    assert calls == 2
    assert len(reviewed) == 1 and "-old" in reviewed[0]
    assert not target.exists()
