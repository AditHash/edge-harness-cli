"""Normalize the subset of Strands events consumed by the terminal."""

from collections.abc import AsyncIterator
from typing import Any

from code_harness.contracts import AgentEvent


async def stream_turn(agent: Any, prompt: str) -> AsyncIterator[AgentEvent]:
    seen_tools: set[str] = set()
    async for event in agent.stream_async(prompt):
        if isinstance(event.get("data"), str):
            yield AgentEvent("text_delta", event["data"])
        elif event.get("current_tool_use", {}).get("name"):
            tool = event["current_tool_use"]
            tool_id = tool.get("toolUseId")
            if tool_id and tool_id not in seen_tools:
                seen_tools.add(tool_id)
                yield AgentEvent("tool_started", tool["name"])
        elif "result" in event:
            result = event["result"]
            context = getattr(result, "invocation_context", None)
            if context is not None:
                usage = context.usage
                yield AgentEvent(
                    "usage",
                    f"Input tokens: {usage.get('inputTokens', 0)}; "
                    f"output tokens: {usage.get('outputTokens', 0)}",
                )
            reason = getattr(result, "stop_reason", "end_turn")
            if reason in {"end_turn", "stop_sequence"}:
                yield AgentEvent("turn_completed")
            else:
                yield AgentEvent("error", f"Agent stopped before completing the turn: {reason}")
