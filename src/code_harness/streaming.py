"""Normalize the subset of Strands events consumed by the terminal."""

from collections.abc import AsyncIterator
from typing import Any

from code_harness.contracts import AgentEvent


def _model_id(agent: Any) -> str:
    """The model that served the latest call (the routed choice in auto mode)."""
    try:
        model_id = agent.model.get_config().get("model_id", "")
    except Exception:  # noqa: BLE001 - display only
        return ""
    return model_id if isinstance(model_id, str) else ""


async def stream_turn(agent: Any, prompt: str) -> AsyncIterator[AgentEvent]:
    seen_tools: dict[str, str] = {}
    manager = getattr(agent, "conversation_manager", None)
    removed_before = getattr(manager, "removed_message_count", 0)
    async for event in agent.stream_async(prompt, limits={"turns": 20, "output_tokens": 32768}):
        if isinstance(event.get("data"), str):
            yield AgentEvent("text_delta", event["data"])
        elif event.get("reasoning") and isinstance(event.get("reasoningText"), str):
            # Reasoning text is not shown; its size drives a live token counter.
            yield AgentEvent("reasoning", event["reasoningText"])
        elif isinstance(event.get("event"), dict) and isinstance(
            event["event"].get("metadata"), dict
        ):
            usage = event["event"]["metadata"].get("usage") or {}
            yield AgentEvent(
                "call_usage",
                _model_id(agent),
                int(usage.get("inputTokens", 0) or 0),
                int(usage.get("outputTokens", 0) or 0),
            )
        elif event.get("current_tool_use", {}).get("name"):
            tool = event["current_tool_use"]
            tool_id = tool.get("toolUseId")
            if tool_id and tool_id not in seen_tools:
                seen_tools[tool_id] = tool["name"]
                yield AgentEvent("tool_started", tool["name"])
        elif isinstance(event.get("message"), dict):
            for block in event["message"].get("content", []):
                result = block.get("toolResult") if isinstance(block, dict) else None
                if isinstance(result, dict):
                    result_id = result.get("toolUseId")
                    tool_name = (
                        seen_tools.get(result_id, "tool") if isinstance(result_id, str) else "tool"
                    )
                    status = "failed" if result.get("status") == "error" else "finished"
                    yield AgentEvent("tool_result", f"{tool_name}: {status}")
        elif "result" in event:
            result = event["result"]
            context = getattr(result, "invocation_context", None)
            if context is not None:
                usage = context.usage
                yield AgentEvent(
                    "usage",
                    f"Input tokens: {usage.get('inputTokens', 0)}; "
                    f"output tokens: {usage.get('outputTokens', 0)}",
                    int(usage.get("inputTokens", 0) or 0),
                    int(usage.get("outputTokens", 0) or 0),
                )
            removed_after = getattr(manager, "removed_message_count", 0)
            if (
                isinstance(removed_after, int)
                and isinstance(removed_before, int)
                and removed_after > removed_before
            ):
                yield AgentEvent(
                    "compacted",
                    f"Context auto-compacted: {removed_after - removed_before} older "
                    "messages summarized.",
                )
            reason = getattr(result, "stop_reason", "end_turn")
            if reason in {"end_turn", "stop_sequence"}:
                yield AgentEvent("turn_completed")
            else:
                yield AgentEvent("error", f"Agent stopped before completing the turn: {reason}")
