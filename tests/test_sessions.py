import asyncio
import json
import os
import re
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
        model.estimate_utilization.return_value = 0.0
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
        model.estimate_utilization.return_value = 0.0
        model.stream.side_effect = model_stream
        return model

    manager = sessions.manager(session_id)
    agent = create_agent(Settings(workspace=workspace), model=new_model(), session_manager=manager)

    async def send(text):
        return [event async for event in stream_turn(agent, text)]

    for text in ("first", "second", "third", "t4", "t5", "t6", "t7", "t8", "t9"):
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
    assert "t9" in str(restored.messages)
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
        model.estimate_utilization.return_value = 0.0
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
        model.estimate_utilization.return_value = 0.0
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
        lambda workspace, session_manager=None, guidance=None, model_id=None, mcp_tools=None: (
            object()
        ),
    )
    runner = CliRunner()
    first = runner.invoke(cli.app, ["chat", "--workspace", str(root)], input="/sessions\n/exit\n")
    assert first.exit_code == 0
    match = re.search(r"Session: ([a-f0-9]{32})", first.output)
    assert match is not None
    session_id = match.group(1)
    assert session_id[:8] in first.output.split("Sessions in this folder")[1]
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

    def make_agent(workspace, session_manager=None, guidance=None, model_id=None, mcp_tools=None):
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


def test_session_details_titles_order_and_prefix(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sessions = Sessions(tmp_path / "state" / "sessions.sqlite", workspace)
    first, empty, second = sessions.create(), sessions.create(), sessions.create()
    sessions.record_turn(first, "fix the\nlogin\x1b[2J bug " + "x" * 100)
    sessions.record_turn(second, "second task")
    sessions.record_turn(second, "follow up")
    infos = sessions.details()
    assert infos[0].session_id == second
    assert {info.session_id for info in infos} == {first, second}  # empty one hidden
    by_id = {info.session_id: info for info in infos}
    assert by_id[second].turns == 2 and by_id[second].title == "second task"
    title = by_id[first].title
    assert title.startswith("fix the login") and "\x1b" not in title and len(title) == 60
    assert empty in {info.session_id for info in sessions.details(empty)}
    assert sessions.latest() == second
    assert sessions.resolve(second[:8]) == second
    with pytest.raises(WorkspaceError):
        sessions.resolve("zz")
    with pytest.raises(WorkspaceError):
        sessions.resolve("ffffffff" if not second.startswith("ffffffff") else "eeeeeeee")


def test_session_details_are_workspace_scoped_and_survive_bad_meta(tmp_path: Path) -> None:
    database = tmp_path / "state" / "sessions.sqlite"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    one = Sessions(database, tmp_path / "a")
    other = Sessions(database, tmp_path / "b")
    mine = one.create()
    one.record_turn(mine, "secret plan")
    assert other.details() == [] and other.latest() is None
    with pytest.raises(WorkspaceError):
        other.resolve(mine[:8])
    one.storage._write(f"meta/{mine}", b"{not json")
    assert one._load_info(mine).turns == 0


def test_older_sessions_without_metadata_stay_listed(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sessions = Sessions(tmp_path / "state" / "sessions.sqlite", workspace)
    old = sessions.create()
    sessions.storage._write(f"session/{old}/scopes/agent/coding-agent/snapshot.json", b"{}")
    assert [info.session_id for info in sessions.details()] == [old]
    assert sessions.latest() is None


def test_chat_end_prints_the_session_id_to_resume(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from code_harness import cli

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(cli, "_make_agent", lambda *a, **k: object())

    async def no_model_call(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(cli, "_show_turn", no_model_call)
    runner = CliRunner()
    arguments = ["chat", "--workspace", str(workspace)]

    empty = runner.invoke(cli.app, arguments, input="/exit\n")
    assert empty.exit_code == 0, empty.output
    assert "--resume" not in empty.output  # Nothing was said, so nothing to resume.
    assert re.search(r"Session [0-9a-f]{32} had no messages", empty.output)

    used = runner.invoke(cli.app, arguments, input="hello\n/exit\n")
    assert used.exit_code == 0, used.output
    match = re.search(r"edge-harness --resume ([0-9a-f]{32})", used.output)
    assert match, used.output

    # EOF ends the chat too, and the printed id really resumes that session.
    again = runner.invoke(cli.app, [*arguments, "--resume", match.group(1)], input="more\n")
    assert again.exit_code == 0, again.output
    assert f"--resume {match.group(1)}" in again.output


def test_ctrl_c_and_crash_still_print_the_session_line(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from code_harness import cli

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "200")
    fake = type("Fake", (), {"messages": [{"role": "user", "content": [{"text": "hi"}]}]})()
    monkeypatch.setattr(cli, "_make_agent", lambda *a, **k: fake)

    for error in (KeyboardInterrupt, RuntimeError):

        def interrupted(status, error=error):
            raise error

        monkeypatch.setattr(cli, "_read_prompt", interrupted)
        result = CliRunner().invoke(cli.app, ["chat", "--workspace", str(workspace)])
        # Messages exist although no turn finished, so the session can be resumed.
        assert re.search(r"edge-harness --resume [0-9a-f]{32}", result.output), result.output


def _messages() -> list[dict]:
    return [
        {"role": "user", "content": [{"text": "first question"}]},
        {
            "role": "assistant",
            "content": [
                {"reasoningContent": {"reasoningText": {"text": "private"}}},
                {"text": "<think>hidden</think>Checking."},
                {"toolUse": {"toolUseId": "1", "name": "read_code", "input": {"path": "a.py"}}},
            ],
        },
        {
            "role": "user",
            "content": [{"toolResult": {"toolUseId": "1", "content": [{"text": "x"}]}}],
        },
        {"role": "assistant", "content": [{"text": "**Done**\x1b[2J"}]},
        {"role": "user", "content": [{"image": {"format": "png", "source": {"bytes": b"1"}}}]},
    ]


def test_history_rows_show_the_conversation_only() -> None:
    from code_harness.ui import history_items

    items, hidden = history_items(_messages())
    assert hidden == 0
    assert items == [
        ("user", "first question"),
        ("assistant", "Checking."),
        ("tool", "read_code a.py"),
        ("assistant", "**Done**[2J"),
        ("user", "[image]"),
    ]
    assert history_items(_messages(), limit=2) == (items[-2:], 3)
    assert history_items(None) == ([], 0)


def test_context_estimate_counts_text_but_not_images() -> None:
    from code_harness.ui import estimated_context_tokens

    text = [{"role": "user", "content": [{"text": "x" * 4000}]}]
    assert 1000 <= estimated_context_tokens(text) < 1100
    image = [{"role": "user", "content": [{"image": {"source": {"bytes": b"y" * 9000}}}]}]
    assert estimated_context_tokens(image) == 0
    assert estimated_context_tokens(None) == 0


def test_resume_replays_history_on_screen(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from code_harness import cli

    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "200")
    fake = type("Fake", (), {"messages": _messages()})()
    monkeypatch.setattr(cli, "_make_agent", lambda *a, **k: fake)
    arguments = ["chat", "--workspace", str(workspace)]

    fresh = CliRunner().invoke(cli.app, arguments, input="/exit\n")
    assert "Earlier in this session" not in fresh.output
    session_id = re.search(r"--resume ([0-9a-f]{32})", fresh.output).group(1)

    resumed = CliRunner().invoke(cli.app, [*arguments, "--resume", session_id], input="/exit\n")
    assert resumed.exit_code == 0, resumed.output
    assert "Earlier in this session" in resumed.output
    assert "first question" in resumed.output and "read_code a.py" in resumed.output
    assert "hidden" not in resumed.output and "private" not in resumed.output
    assert "\x1b[2J" not in resumed.output
