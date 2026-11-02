import asyncio
from types import SimpleNamespace

from code_harness.streaming import stream_turn


class FakeAgent:
    async def stream_async(self, prompt):
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
        async def stream_async(self, prompt):
            tool = {"toolUseId": "one", "name": "read_code"}
            yield {"current_tool_use": tool}
            yield {"current_tool_use": tool}
            yield {"result": SimpleNamespace(stop_reason="max_tokens", invocation_context=None)}

    async def collect():
        return [event async for event in stream_turn(TruncatedAgent(), "hi")]

    events = asyncio.run(collect())
    assert [event.kind for event in events] == ["tool_started", "error"]
    assert "max_tokens" in events[-1].text
