import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.instructions import Guidance
from code_harness.sessions import Sessions, SQLiteStorage
from code_harness.streaming import stream_turn
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
    with pytest.raises(WorkspaceError, match="protected directory"):
        Sessions(workspace / "sessions.sqlite", workspace)

    async def exercise() -> None:
        storage = SQLiteStorage(db)
        await storage.write("test/a", b"hello")
        await storage.write("test/b", b"world")
        assert await storage.list("test/") == ["test/a", "test/b"]
        assert await storage.read("test/a") == b"hello"
        await storage.delete("test/a")
        assert await storage.read("test/a") is None

    asyncio.run(exercise())


def test_strands_session_restores_conversation_across_agents(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sessions = Sessions(tmp_path / "state" / "sessions.sqlite", workspace)
    session_id = sessions.create()
    requests = []

    async def model_stream(messages, *args, **kwargs):
        requests.append([message.copy() for message in messages])
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "remembered response"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }

    def new_agent():
        model = MagicMock(spec=Model)
        model.stateful = False
        model.get_config.return_value = {}
        model.stream.side_effect = model_stream
        return create_agent(
            Settings(workspace=workspace), model=model, session_manager=sessions.manager(session_id)
        )

    async def send(agent, message):
        return [event async for event in stream_turn(agent, message)]

    asyncio.run(send(new_agent(), "remember this"))
    asyncio.run(send(new_agent(), "continue"))
    assert len(requests) == 2
    assert "remember this" in str(requests[1])
    assert "remembered response" in str(requests[1])
    assert "continue" in str(requests[1])


def test_compacted_history_is_saved_and_restored(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sessions = Sessions(tmp_path / "state" / "sessions.sqlite", workspace)
    session_id = sessions.create()

    async def model_stream(messages, *args, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "response"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }

    def new_model():
        model = MagicMock(spec=Model)
        model.stateful = False
        model.get_config.return_value = {}
        model.stream.side_effect = model_stream
        return model

    manager = sessions.manager(session_id)
    agent = create_agent(Settings(workspace=workspace), model=new_model(), session_manager=manager)

    async def send(text):
        return [event async for event in stream_turn(agent, text)]

    for text in ("first", "second", "third"):
        asyncio.run(send(text))
    before = len(agent.messages)
    agent.conversation_manager.reduce_context(agent)
    assert 0 < len(agent.messages) < before
    asyncio.run(manager.save_snapshot(agent, is_latest=True))
    restored = create_agent(
        Settings(workspace=workspace),
        model=new_model(),
        session_manager=sessions.manager(session_id),
    )
    assert len(restored.messages) == len(agent.messages)
    assert "third" in str(restored.messages)
    assert "first" not in str(restored.messages)


def test_resumed_session_uses_current_guidance_not_stored_system_prompt(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sessions = Sessions(tmp_path / "state" / "sessions.sqlite", workspace)
    session_id = sessions.create()

    async def model_stream(messages, *args, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "done"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }

    def new_model():
        model = MagicMock(spec=Model)
        model.stateful = False
        model.get_config.return_value = {}
        model.stream.side_effect = model_stream
        return model

    first = create_agent(
        Settings(workspace=workspace),
        model=new_model(),
        session_manager=sessions.manager(session_id),
        guidance=[Guidance("AGENTS.md", "previous guidance")],
    )

    async def send():
        return [event async for event in stream_turn(first, "hello")]

    asyncio.run(send())
    resumed = create_agent(
        Settings(workspace=workspace),
        model=new_model(),
        session_manager=sessions.manager(session_id),
        guidance=[Guidance("AGENTS.md", "current guidance")],
    )
    assert "current guidance" in str(resumed.system_prompt)
    assert "previous guidance" not in str(resumed.system_prompt)


@pytest.mark.skipif(os.name != "nt", reason="secure edits currently require Windows")
def test_failed_turn_does_not_resume_or_replay_approved_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "app.py"
    target.write_bytes(b"old\n")
    sessions = Sessions(tmp_path / "state" / "sessions.sqlite", workspace)
    session_id = sessions.create()
    reviews = []
    calls = 0

    async def model_stream(messages, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated model failure after edit")
        yield {"messageStart": {"role": "assistant"}}
        yield {
            "contentBlockStart": {"start": {"toolUse": {"toolUseId": "edit1", "name": "edit_code"}}}
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
        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }

    def new_agent():
        model = MagicMock(spec=Model)
        model.stateful = False
        model.get_config.return_value = {}
        model.stream.side_effect = model_stream
        return create_agent(
            Settings(workspace=workspace),
            model=model,
            session_manager=sessions.manager(session_id),
            review=lambda proposal: reviews.append(proposal.id) or True,
        )

    async def send(agent):
        return [event async for event in stream_turn(agent, "edit")]

    with pytest.raises(Exception, match="simulated model failure"):
        asyncio.run(send(new_agent()))
    assert target.read_bytes() == b"new\n" and len(reviews) == 1
    restored = new_agent()
    assert not restored.messages
    assert target.read_bytes() == b"new\n" and len(reviews) == 1


def test_cli_lists_and_resumes_workspace_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import re

    from typer.testing import CliRunner

    from code_harness import cli

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "private"))
    monkeypatch.setattr(
        cli,
        "_make_agent",
        lambda workspace, session_manager=None, guidance=None, model_id=None: object(),
    )
    runner = CliRunner()
    first = runner.invoke(cli.app, ["chat", "--workspace", str(root)], input="/sessions\n/exit\n")
    assert first.exit_code == 0
    match = re.search(r"Session: ([a-f0-9]{32})", first.output)
    assert match is not None
    session_id = match.group(1)
    assert first.output.count(session_id) >= 2
    second = runner.invoke(
        cli.app,
        ["chat", "--workspace", str(root), "--resume", session_id],
        input="/status\n/exit\n",
    )
    assert second.exit_code == 0 and session_id in second.output


def test_cli_model_switch_is_explicit_and_preserves_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from code_harness import cli

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "private"))
    models = []

    def make_agent(workspace, session_manager=None, guidance=None, model_id=None):
        models.append(model_id)
        return object()

    monkeypatch.setattr(cli, "_make_agent", make_agent)
    result = CliRunner().invoke(
        cli.app,
        ["chat", "--workspace", str(root)],
        input="/model us.example-v1\n/model\n/exit\n",
    )
    assert result.exit_code == 0
    assert models[-1] == "us.example-v1"
    assert "Model: us.example-v1" in result.output
