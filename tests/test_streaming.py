import asyncio

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
