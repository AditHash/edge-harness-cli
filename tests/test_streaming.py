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


def test_reasoning_and_per_call_usage_are_reported() -> None:
    class ThinkingAgent:
        async def stream_async(self, prompt, **kwargs):
            yield {"reasoningText": "let me think", "delta": {}, "reasoning": True}
            yield {"reasoningText": " more", "delta": {}, "reasoning": True}
            yield {"event": {"metadata": {"usage": {"inputTokens": 120, "outputTokens": 45}}}}
            yield {"data": "answer"}
            yield {"result": SimpleNamespace(stop_reason="end_turn", invocation_context=None)}

    async def collect():
        return [event async for event in stream_turn(ThinkingAgent(), "hi")]

    events = asyncio.run(collect())
    assert [e.kind for e in events] == [
        "reasoning",
        "reasoning",
        "call_usage",
        "text_delta",
        "turn_completed",
    ]
    assert (events[2].input_tokens, events[2].output_tokens) == (120, 45)


def test_cli_thinking_meter_counts_and_clears(monkeypatch) -> None:
    from code_harness import cli

    class Status:
        def __init__(self):
            self.messages: list[str] = []
            self.stopped = False

        def start(self):
            pass

        def update(self, message):
            self.messages.append(message)

        def stop(self):
            self.stopped = True

    created: list[Status] = []
    printed: list[str] = []

    def fake_status(message, spinner=None):
        status = Status()
        status.messages.append(message)
        created.append(status)
        return status

    monkeypatch.setattr(cli.console, "status", fake_status)
    monkeypatch.setattr(
        cli.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else "")
    )
    shown = cli._ThinkingMeter(show=True)
    shown.add("step one ")
    shown.add("x" * 391)
    shown.stop()
    assert "step one " in printed and any("thinking: ~100 tokens" in line for line in printed)
    assert created == []  # visible mode prints the text, no spinner

    printed.clear()
    hidden = cli._ThinkingMeter(show=False)
    hidden.add("x" * 400)
    hidden.add("x" * 400)
    assert created[0].messages[-1] == "Thinking... ~200 tokens"
    assert not any("x" * 400 in line for line in printed)
    hidden.stop()
    assert created[0].stopped and any("thinking: ~200 tokens" in line for line in printed)
    hidden.stop()  # idempotent


def test_call_usage_names_the_serving_model() -> None:
    class Model:
        def get_config(self):
            return {"model_id": "in.anthropic.claude-haiku-4-5"}

    class RoutedAgent:
        model = Model()

        async def stream_async(self, prompt, **kwargs):
            yield {"event": {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 2}}}}
            yield {"result": SimpleNamespace(stop_reason="end_turn", invocation_context=None)}

    async def collect():
        return [event async for event in stream_turn(RoutedAgent(), "hi")]

    call = asyncio.run(collect())[0]
    assert call.kind == "call_usage" and call.text == "in.anthropic.claude-haiku-4-5"


def test_tool_call_events_name_their_target() -> None:
    from code_harness.streaming import tool_summary

    class ToolAgent:
        async def stream_async(self, prompt, **kwargs):
            yield {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "1",
                                "name": "read_code",
                                "input": {"path": "README.md"},
                            }
                        },
                        {"toolUse": {"toolUseId": "2", "name": "list_repo", "input": {}}},
                    ],
                }
            }
            yield {"result": SimpleNamespace(stop_reason="end_turn", invocation_context=None)}

    async def collect():
        return [e async for e in stream_turn(ToolAgent(), "hi")]

    calls = [e.text for e in asyncio.run(collect()) if e.kind == "tool_call"]
    assert calls == ["read_code README.md", "list_repo"]
    assert tool_summary("run_sandboxed", {"command": "a\x1b[2Jb" + "x" * 100}).startswith(
        "run_sandboxed a [2Jb"
    )
    assert len(tool_summary("t", {"path": "y" * 200})) == 82
