"""Scenario C: failing test -> approved fix -> rerun, through the real Strands agent."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.streaming import stream_turn

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("HARNESS_TEST_WSL") != "1",
    reason="needs Windows edits and opt-in WSL sandbox",
)

RUN = {"command": "python3 -m unittest -q test_calc"}


def _script(steps):
    """Yield one scripted tool call (name, input) per model call, then final text."""
    state = {"n": 0}
    results: list[str] = []

    async def stream(messages, *args, **kwargs):
        index = state["n"]
        state["n"] += 1
        last = messages[-1]["content"][0] if messages else {}
        if "toolResult" in last:
            results.append(json.dumps(last["toolResult"]["content"]))
        yield {"messageStart": {"role": "assistant"}}
        if index < len(steps):
            name, arguments = steps[index]
            start = {"toolUseId": f"t{index}", "name": name}
            yield {"contentBlockStart": {"start": {"toolUse": start}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "Fixed and verified."}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        usage = {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}
        yield {"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}}

    return stream, results


def test_debug_cycle_fixes_bug_and_reports_real_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (root / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    stream, results = _script(
        [
            ("run_sandboxed", RUN),
            ("edit_code", {"path": "calc.py", "new_content": "def add(a, b):\n    return a + b\n"}),
            ("run_sandboxed", RUN),
        ]
    )
    model = MagicMock(spec=Model)
    model.stateful = False
    model.get_config.return_value = {}
    model.stream.side_effect = stream
    commands: list[str] = []
    diffs: list[str] = []

    def approve_command(command: str) -> bool:
        commands.append(command)
        return True

    def approve_edit(proposal) -> bool:
        diffs.append(proposal.diff)
        return True

    agent = create_agent(
        Settings(workspace=root),
        model=model,
        review=approve_edit,
        review_command=approve_command,
    )

    async def run():
        return [event async for event in stream_turn(agent, "fix the failing test")]

    asyncio.run(run())
    assert commands == [RUN["command"]] * 2
    assert len(diffs) == 1 and "-    return a - b" in diffs[0] and "+    return a + b" in diffs[0]
    assert (root / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    first, _edit, second = results
    assert '\\"exit_code\\": 1' in first or '"exit_code": 1' in first.replace('\\"', '"')
    assert '"exit_code": 0' in second.replace('\\"', '"')
