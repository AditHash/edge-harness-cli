import asyncio
from types import SimpleNamespace

from code_harness.streaming import stream_turn


class FakeAgent:
    async def stream_async(self, prompt, **kwargs):
        yield {"data": "Hello"}
        yield {"data": " world"}
        yield {"result": object()}


def test_stream_does_not_duplicate_final_result() -> None:
    async def collect():
        return [event async for event in stream_turn(FakeAgent(), "hi")]

    events = asyncio.run(collect())
    assert [event.text for event in events] == ["Hello", " world", ""]
    assert events[-1].kind == "turn_completed"


def test_tool_events_are_deduplicated_and_truncation_is_reported() -> None:
    class TruncatedAgent:
        async def stream_async(self, prompt, **kwargs):
            tool = {"toolUseId": "one", "name": "read_code"}
            yield {"current_tool_use": tool}
            yield {"current_tool_use": tool}
            yield {"result": SimpleNamespace(stop_reason="max_tokens", invocation_context=None)}

    async def collect():
        return [event async for event in stream_turn(TruncatedAgent(), "hi")]

    events = asyncio.run(collect())
    assert [event.kind for event in events] == ["tool_started", "error"]
    assert "max_tokens" in events[-1].text


def test_tool_result_is_reported_without_exposing_tool_contents() -> None:
    class ToolAgent:
        async def stream_async(self, prompt, **kwargs):
            yield {"current_tool_use": {"toolUseId": "one", "name": "read_code"}}
            yield {
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": "one",
                                "status": "error",
                                "content": [{"text": "sensitive"}],
                            }
                        }
                    ],
                }
            }
            yield {"result": SimpleNamespace(stop_reason="end_turn", invocation_context=None)}

    async def collect():
        return [event async for event in stream_turn(ToolAgent(), "hi")]

    events = asyncio.run(collect())
    assert [event.kind for event in events] == ["tool_started", "tool_result", "turn_completed"]
    assert events[1].text == "read_code: failed"
    assert "sensitive" not in str(events)


def test_usage_events_feed_cumulative_tracker() -> None:
    from code_harness.usage import UsageTracker

    class UsageAgent:
        async def stream_async(self, prompt, **kwargs):
            context = SimpleNamespace(usage={"inputTokens": 1000, "outputTokens": 500})
            yield {"result": SimpleNamespace(stop_reason="end_turn", invocation_context=context)}

    tracker = UsageTracker(3.0, 15.0)

    async def collect():
        for _ in range(2):
            async for event in stream_turn(UsageAgent(), "hi"):
                if event.kind == "usage":
                    tracker.add(event.input_tokens, event.output_tokens)

    asyncio.run(collect())
    assert (tracker.input_tokens, tracker.output_tokens, tracker.turns) == (2000, 1000, 2)
    assert tracker.cost is not None and abs(tracker.cost - 0.021) < 1e-9
    assert "Cost unknown" in UsageTracker().summary()
