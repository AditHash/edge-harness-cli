import asyncio
from typing import Any

import pytest
from strands.types.exceptions import ContextWindowOverflowException, ModelThrottledException

from code_harness.routing import RoutedModel, classify


class Denied(Exception):
    response = {"Error": {"Code": "AccessDeniedException"}}  # noqa: RUF012


class FakeModel:
    def __init__(self, name: str, fail: Exception | None = None, fail_after_start: bool = False):
        self.name, self.fail, self.fail_after_start = name, fail, fail_after_start
        self.calls = 0

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        if self.fail is not None and not self.fail_after_start:
            raise self.fail
        yield {"contentBlockDelta": {"delta": {"text": self.name}}}
        if self.fail is not None:
            raise self.fail

    def get_config(self) -> dict[str, Any]:
        return {"model_id": self.name}

    def update_config(self, **kwargs) -> None:
        pass


def _collect(model: RoutedModel) -> list[str]:
    async def run():
        return [e["contentBlockDelta"]["delta"]["text"] async for e in model.stream([])]

    return asyncio.run(run())


def test_classify() -> None:
    assert classify(Denied()) == "drop"
    assert classify(ModelThrottledException("slow")) == "cooldown"
    assert classify(ContextWindowOverflowException("big")) == "bigger"
    assert classify(ValueError("bug")) is None


def test_failover_on_no_access_then_sticks_to_working_model() -> None:
    first, second = FakeModel("a", Denied()), FakeModel("b")
    notes: list[str] = []
    routed = RoutedModel([("a", first, 1000), ("b", second, 500)], notify=notes.append)
    assert _collect(routed) == ["b"]
    assert _collect(routed) == ["b"]
    assert first.calls == 1  # dropped for the session after no access
    assert routed.get_config()["model_id"] == "b"
    assert any("no access" in note for note in notes)


def test_throttled_model_cools_down_then_returns() -> None:
    clock = [0.0]
    first, second = FakeModel("a", ModelThrottledException("slow")), FakeModel("b")
    routed = RoutedModel([("a", first, 1000), ("b", second, 1000)], clock=lambda: clock[0])
    assert _collect(routed) == ["b"]
    first.fail = None
    assert _collect(routed) == ["b"]  # still cooling down
    clock[0] = 1000.0
    assert _collect(routed) == ["a"]  # back to the preferred model


def test_overflow_escalates_to_larger_context_only() -> None:
    small = FakeModel("small", ContextWindowOverflowException("big"))
    smaller = FakeModel("smaller")
    large = FakeModel("large")
    routed = RoutedModel([("small", small, 200), ("smaller", smaller, 100), ("large", large, 900)])
    assert _collect(routed) == ["large"]
    assert smaller.calls == 0


def test_error_after_output_is_not_retried_elsewhere() -> None:
    first = FakeModel("a", Denied(), fail_after_start=True)
    second = FakeModel("b")
    routed = RoutedModel([("a", first, 1000), ("b", second, 1000)])
    with pytest.raises(Denied):
        _collect(routed)
    assert second.calls == 0


def test_unrecoverable_error_is_raised_and_all_failing_reports_last() -> None:
    routed = RoutedModel([("a", FakeModel("a", ValueError("bug")), 10)])
    with pytest.raises(ValueError):
        _collect(routed)
    both = RoutedModel([("a", FakeModel("a", Denied()), 10), ("b", FakeModel("b", Denied()), 10)])
    with pytest.raises(Denied):
        _collect(both)


def test_compaction_window_is_smallest_live_candidate() -> None:
    routed = RoutedModel([("a", FakeModel("a"), 1000), ("b", FakeModel("b"), 200)])
    assert routed.context_window_limit == 200
    routed.dropped.add("b")
    assert routed.context_window_limit == 1000
    assert routed.estimate_utilization(500) == 0.5


def test_create_agent_builds_router_from_probed_cache(tmp_path) -> None:
    from code_harness import catalog
    from code_harness.agent import create_agent
    from code_harness.config import Settings

    catalog.save_cache(
        tmp_path,
        {
            "listing": {
                "india": ["in.anthropic.claude-haiku-4-5-20251001-v1:0", "in.x"],
                "mumbai": ["qwen.qwen3-coder-480b-a35b-v1:0"],
                "time": 9e12,
            },
            "probes": {
                "in.anthropic.claude-haiku-4-5-20251001-v1:0": "ok",
                "in.x": "no_access",
                "qwen.qwen3-coder-480b-a35b-v1:0": "ok",
            },
        },
    )
    settings = Settings(
        workspace=tmp_path,
        bedrock_model_id="auto",
        aws_region="ap-south-1",
        harness_state_dir=tmp_path,
    )
    agent = create_agent(settings)
    assert isinstance(agent.model, RoutedModel)
    assert [c[0] for c in agent.model.candidates] == [
        "in.anthropic.claude-haiku-4-5-20251001-v1:0",
        "qwen.qwen3-coder-480b-a35b-v1:0",
    ]
    mumbai_only = create_agent(settings.model_copy(update={"harness_residency": "mumbai"}))
    assert [c[0] for c in mumbai_only.model.candidates] == ["qwen.qwen3-coder-480b-a35b-v1:0"]
