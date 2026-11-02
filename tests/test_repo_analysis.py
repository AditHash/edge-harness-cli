"""Scenario A plumbing: the agent explores a realistic repo through guarded tools.

A scripted model cannot judge explanation quality; this test verifies what the model is given:
correct, cited, ranked content from the right files and nothing from protected files. The
quality of a real explanation needs a live model (Scenario G).
"""

import asyncio
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock

from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.streaming import stream_turn

REPO = Path(__file__).parent / "fixtures" / "shop_repo"


def test_agent_explores_realistic_repo_and_never_sees_secrets(tmp_path: Path) -> None:
    root = tmp_path / "shop_repo"
    shutil.copytree(REPO, root)
    (root / ".env").write_text("SHOP_API_KEY=super-secret-key\n")
    (root / "shop" / "id_rsa").write_text("PRIVATE KEY MATERIAL\n")
    steps = [
        ("list_repo", {}),
        ("find_relevant_code", {"question": "where is the bulk discount applied to the total"}),
        ("read_code", {"path": "shop/orders.py"}),
        ("read_code", {"path": ".env"}),
        ("search_code", {"query": "KeyError"}),
        ("git_status", {}),
    ]
    results: list[str] = []
    state = {"n": 0}

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
            yield {"contentBlockDelta": {"delta": {"text": "analysis"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        usage = {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}
        yield {"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}}

    model = MagicMock(spec=Model)
    model.stateful = False
    model.get_config.return_value = {}
    model.estimate_utilization.return_value = 0.0
    model.stream.side_effect = stream
    agent = create_agent(Settings(workspace=root), model=model)

    async def run():
        return [event async for event in stream_turn(agent, "explain the architecture")]

    events = asyncio.run(run())
    assert events[-1].kind == "turn_completed"
    listing, ranked, orders, env, search, git = results
    for name in ("shop/catalog.py", "shop/cart.py", "shop/orders.py", "shop/storage.py"):
        assert name in listing
    assert ".env" not in listing and "id_rsa" not in listing
    assert "shop/cart.py:" in ranked and "BULK_DISCOUNT_PERCENT" in ranked
    assert "place_order" in orders and "JsonStorage" in orders
    assert "Access denied" in env and "super-secret" not in env
    assert "shop/catalog.py" in search
    assert "Git unavailable" in git  # the copied fixture is not a repository
    everything = " ".join(results)
    assert "super-secret-key" not in everything and "PRIVATE KEY" not in everything
