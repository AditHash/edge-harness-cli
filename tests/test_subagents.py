import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from strands.models import Model
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.streaming import stream_turn, tool_summary
from code_harness.usage import MAX_SUBAGENTS_PER_TURN, SubagentLedger

READ_ONLY = {
    "list_repo",
    "read_code",
    "search_code",
    "find_relevant_code",
    "git_status",
    "git_diff",
    "git_log",
}


class Script:
    """A fake model: the main agent and subagents each follow their own scripted replies."""

    def __init__(self, main: list[Any], sub: list[Any] | None = None) -> None:
        self.main = list(main)
        self.sub = list(sub or [])
        self.sub_tools: list[set[str]] = []
        self.main_requests: list[str] = []
        self.sub_requests: list[str] = []
        self.lock = threading.Lock()

    def model(self) -> Any:
        model = MagicMock(spec=Model)
        model.stateful = False
        model.get_config.return_value = {}
        model.estimate_utilization.return_value = 0.0
        model.stream.side_effect = self.stream
        return model

    async def stream(
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **_: Any
    ):
        is_sub = "You are a subagent" in str(system_prompt)
        with self.lock:
            if is_sub:
                self.sub_tools.append({spec["name"] for spec in tool_specs or []})
                self.sub_requests.append(str(messages))
                step = self.sub.pop(0) if self.sub else "sub done"
            else:
                self.main_requests.append(str(messages))
                step = self.main.pop(0) if self.main else "main done"
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(step, str):
            yield {"contentBlockDelta": {"delta": {"text": step}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            for index, (name, arguments) in enumerate(step):
                use = {"toolUseId": f"{name}{index}{time.monotonic_ns()}", "name": name}
                yield {"contentBlockStart": {"start": {"toolUse": use}}}
                yield {
                    "contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}
                }
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        usage = {"inputTokens": 10, "outputTokens": 2, "totalTokens": 12, "cacheReadInputTokens": 5}
        yield {"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}}


def spawn(role: str, task: str = "do the task") -> tuple[str, dict[str, str]]:
    return ("spawn_subagent", {"role": role, "task": task})


def run_turn(agent: Any, prompt: str = "go") -> list[Any]:
    async def collect() -> list[Any]:
        return [event async for event in stream_turn(agent, prompt)]

    return asyncio.run(collect())


def direct(agent: Any) -> Any:
    return agent.tool_registry.registry["spawn_subagent"]._tool_func


def test_report_returns_to_main_agent_and_usage_is_counted(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def needle(): pass\n")
    (tmp_path / ".env").write_text("needle SECRET")
    script = Script(
        main=[[spawn("explore", "find needle")], "All done"],
        sub=[[("search_code", {"query": "needle"})], "needle is at app.py:1"],
    )
    notes: list[str] = []
    agent = create_agent(Settings(workspace=tmp_path), model=script.model(), notify=notes.append)
    events = run_turn(agent)

    assert "needle is at app.py:1" in script.main_requests[-1]
    assert "find needle" in script.sub_requests[0]
    assert "go" not in script.sub_requests[0].replace("role", "")  # no parent conversation
    assert "SECRET" not in str(script.sub_requests) + str(script.main_requests)
    assert any("search_code needle" in note for note in notes)
    total = next(event for event in events if event.kind == "usage")
    # 2 main calls + 2 subagent calls.
    assert (total.input_tokens, total.output_tokens, total.cached_tokens) == (40, 8, 20)
    assert agent.subagents.drain() == (0, 0, 0)


def test_only_the_final_message_is_the_report(tmp_path: Path) -> None:
    script = Script(main=[[spawn("explore")], "ok"], sub=[[("list_repo", {})], "final report"])
    run_turn(create_agent(Settings(workspace=tmp_path), model=script.model()))
    assert "final report" in script.main_requests[-1]


def test_read_only_roles_have_no_write_or_spawn_tools(tmp_path: Path) -> None:
    script = Script(main=[[spawn("explore"), spawn("review")], "ok"], sub=["a", "b"])
    review = MagicMock(return_value=True)
    agent = create_agent(
        Settings(workspace=tmp_path), model=script.model(), review=review, review_command=review
    )
    run_turn(agent)
    assert script.sub_tools == [READ_ONLY, READ_ONLY]


def test_edit_role_has_edit_tools_but_cannot_spawn(tmp_path: Path) -> None:
    script = Script(main=[[spawn("edit")], "ok"], sub=["changed nothing"])
    agent = create_agent(
        Settings(workspace=tmp_path), model=script.model(), review=MagicMock(return_value=False)
    )
    run_turn(agent)
    assert script.sub_tools[0] >= {"edit_code", "replace_in_file", "delete_code"}
    assert "spawn_subagent" not in script.sub_tools[0]


def test_subagent_edit_needs_the_same_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("old\n")
    arguments = {"path": "a.txt", "old_text": "old\n", "new_text": "new\n"}
    script = Script(
        main=[[spawn("edit")], "ok"], sub=[[("replace_in_file", arguments)], "was refused"]
    )
    review = MagicMock(return_value=False)
    run_turn(create_agent(Settings(workspace=root), model=script.model(), review=review))
    assert review.call_count == 1
    assert target.read_text() == "old\n"


def test_edit_role_is_absent_without_a_review_path(tmp_path: Path) -> None:
    script = Script(main=[])
    agent = create_agent(Settings(workspace=tmp_path), model=script.model())
    result = direct(agent)("edit", "change it")
    assert "unknown role" in result and "explore" in result and "edit," not in result
    assert script.sub_tools == []


def test_mcp_tools_never_reach_subagents(tmp_path: Path) -> None:
    mcp_definition = MagicMock()
    mcp_definition.name = "remote_action"
    mcp_definition.description = "x"
    mcp_definition.inputSchema = {"type": "object", "properties": {}}
    mcp_definition.outputSchema = None
    mcp_tool = MCPAgentTool(mcp_definition, MagicMock())
    script = Script(main=[[spawn("explore"), spawn("edit")], "ok"], sub=["a", "b"])
    agent = create_agent(
        Settings(workspace=tmp_path),
        model=script.model(),
        review=MagicMock(return_value=False),
        mcp_tools=[mcp_tool],
        mcp_review=MagicMock(return_value=False),
    )
    assert "remote_action" in agent.tool_registry.registry
    run_turn(agent)
    assert all("remote_action" not in names for names in script.sub_tools)


def test_turn_budget_and_empty_task(tmp_path: Path) -> None:
    script = Script(main=[])
    agent = create_agent(Settings(workspace=tmp_path), model=script.model())
    run = direct(agent)
    assert "empty" in run("explore", "   ")
    for _ in range(MAX_SUBAGENTS_PER_TURN):
        assert "limit" not in run("explore", "task")
    assert "limit is reached" in run("explore", "task")
    agent.subagents.begin_turn()
    assert "limit" not in run("explore", "task")


def test_parallel_spawns_run_together_with_a_cap(tmp_path: Path) -> None:
    active = {"now": 0, "peak": 0}
    guard = threading.Lock()
    script = Script(main=[[spawn("explore") for _ in range(5)], "ok"])
    inner = script.stream

    async def tracked(messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kw: Any):
        is_sub = "You are a subagent" in str(system_prompt)
        if is_sub:
            with guard:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            await asyncio.sleep(0.2)
        async for event in inner(messages, tool_specs, system_prompt, **kw):
            yield event
        if is_sub:
            with guard:
                active["now"] -= 1

    model = script.model()
    model.stream.side_effect = tracked
    run_turn(create_agent(Settings(workspace=tmp_path), model=model))
    assert len(script.sub_tools) == 5
    assert active["peak"] == 3


def test_stopped_turn_stops_waiting_subagents(tmp_path: Path) -> None:
    script = Script(main=[])
    agent = create_agent(Settings(workspace=tmp_path), model=script.model())
    agent.subagents.stop.set()
    assert "cancelled" in direct(agent)("explore", "task")
    assert script.sub_tools == []


def test_stream_turn_sets_stop_when_a_turn_ends_or_is_abandoned(tmp_path: Path) -> None:
    script = Script(main=["hello", "again"])
    agent = create_agent(Settings(workspace=tmp_path), model=script.model())
    run_turn(agent)
    assert agent.subagents.stop.is_set()

    async def abandon() -> None:
        stream = stream_turn(agent, "go")
        await stream.__anext__()
        assert not agent.subagents.stop.is_set()
        await stream.aclose()

    asyncio.run(abandon())
    assert agent.subagents.stop.is_set()


def test_failure_does_not_leak_provider_text(tmp_path: Path) -> None:
    async def explode(*args: Any, **kwargs: Any):
        raise RuntimeError("secret provider text")
        yield

    model = Script(main=[]).model()
    model.stream.side_effect = explode
    agent = create_agent(Settings(workspace=tmp_path), model=model)
    result = direct(agent)("explore", "task")
    assert "failed" in result and "secret" not in result


def test_can_be_disabled(tmp_path: Path) -> None:
    settings = Settings(workspace=tmp_path, harness_enable_subagents=False)
    agent = create_agent(settings, model=Script(main=[]).model())
    assert "spawn_subagent" not in agent.tool_registry.registry
    assert "spawn_subagent" not in str(agent.system_prompt)


def test_ledger_labels_and_summary() -> None:
    ledger = SubagentLedger()
    assert ledger.reserve("explore") == "explore-1"
    ledger.begin_turn()
    assert ledger.reserve("test") == "test-2"
    assert tool_summary("spawn_subagent", {"role": "test", "task": "run pytest"}) == (
        "spawn_subagent run pytest"
    )
