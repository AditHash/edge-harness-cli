"""Normalize the subset of Strands events consumed by the terminal."""

from collections.abc import AsyncIterator
from typing import Any

from code_harness.contracts import AgentEvent


async def stream_turn(agent: Any, prompt: str) -> AsyncIterator[AgentEvent]:
    async for event in agent.stream_async(prompt):
        if isinstance(event.get("data"), str):
            yield AgentEvent("text_delta", event["data"])
        elif event.get("current_tool_use", {}).get("name"):
            yield AgentEvent("tool_started", event["current_tool_use"]["name"])
        elif "result" in event:
            yield AgentEvent("turn_completed")
