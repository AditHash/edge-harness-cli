"""Normalize the subset of Strands events consumed by the terminal."""

import threading
from collections.abc import AsyncIterator
from typing import Any, Literal

from code_harness.contracts import AgentEvent
from code_harness.usage import SubagentLedger

SUMMARY_KEYS = ("path", "command", "question", "query", "url", "task")


ThinkKind = Literal["text_delta", "reasoning"]


class ThinkFilter:
    """Moves a leading `<think>...</think>` block out of a model's reply text.

    Some models write their reasoning into the reply instead of a reasoning block. Only a block at
    the very start of a message counts, so a reply that merely mentions the tag stays intact. An
    unclosed block stays reasoning, which the interface still shows as thinking.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self.state = "start"
        self.buffer = ""

    def feed(self, chunk: str) -> list[tuple[ThinkKind, str]]:
        self.buffer += chunk
        out: list[tuple[ThinkKind, str]] = []
        while self.buffer:
            if self.state == "text":
                out.append(("text_delta", self.buffer))
                self.buffer = ""
            elif self.state == "start":
                head = self.buffer.lstrip()
                if not head or (len(head) < len(self.OPEN) and self.OPEN.startswith(head)):
                    break  # Not enough text yet to decide.
                if head.startswith(self.OPEN):
                    self.state, self.buffer = "think", head[len(self.OPEN) :]
                else:
                    self.state = "text"
            elif self.state == "think":
                before, closed, after = self.buffer.partition(self.CLOSE)
                if closed:
                    if before:
                        out.append(("reasoning", before))
                    self.state, self.buffer = "after", after
                    continue
                # Hold back only what could be the start of the closing tag.
                keep = next(
                    (
                        size
                        for size in range(len(self.CLOSE) - 1, 0, -1)
                        if self.buffer.endswith(self.CLOSE[:size])
                    ),
                    0,
                )
                ready = self.buffer[: len(self.buffer) - keep]
                if ready:
                    out.append(("reasoning", ready))
                self.buffer = self.buffer[len(ready) :]
                break
            else:  # "after": drop the blank space between the block and the answer.
                self.buffer = self.buffer.lstrip()
                if self.buffer:
                    self.state = "text"
        return out

    def finish_message(self) -> list[tuple[ThinkKind, str]]:
        """Release held text at the end of a message and get ready for the next one."""
        held, state = self.buffer, self.state
        self.state, self.buffer = "start", ""
        if not held:
            return []
        return [("reasoning" if state == "think" else "text_delta", held)]


def tool_summary(name: str, arguments: Any) -> str:
    """`name target` using the most telling argument; control characters removed, bounded."""
    target = ""
    if isinstance(arguments, dict):
        for key in SUMMARY_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip()
                break
    target = "".join(ch if ch.isprintable() else " " for ch in target)
    if len(target) > 80:
        target = target[:77] + "..."
    return f"{name} {target}".rstrip()


def _cached(usage: dict[str, Any]) -> int:
    """Prompt tokens served from or written to the provider cache (not in inputTokens)."""
    return int(usage.get("cacheReadInputTokens", 0) or 0) + int(
        usage.get("cacheWriteInputTokens", 0) or 0
    )


def _model_id(agent: Any) -> str:
    """The model that served the latest call (the routed choice in auto mode)."""
    try:
        model_id = agent.model.get_config().get("model_id", "")
    except Exception:  # noqa: BLE001 - display only
        return ""
    return model_id if isinstance(model_id, str) else ""


async def stream_turn(
    agent: Any, prompt: Any, cancel_signal: threading.Event | None = None
) -> AsyncIterator[AgentEvent]:
    ledger = getattr(agent, "subagents", None)
    if not isinstance(ledger, SubagentLedger):
        ledger = None
    if ledger is not None:
        ledger.begin_turn()
    extra = {} if cancel_signal is None else {"cancel_signal": cancel_signal}
    try:
        async for item in _stream_events(agent, prompt, ledger, extra):
            yield item
    finally:
        # Cancelling a turn must not leave subagents working (or asking for approval) behind it.
        if ledger is not None:
            ledger.stop.set()


async def _stream_events(
    agent: Any, prompt: Any, ledger: SubagentLedger | None, extra: dict[str, Any]
) -> AsyncIterator[AgentEvent]:
    seen_tools: dict[str, str] = {}
    think = ThinkFilter()
    manager = getattr(agent, "conversation_manager", None)
    removed_before = getattr(manager, "removed_message_count", 0)
    async for event in agent.stream_async(
        prompt, limits={"turns": 20, "output_tokens": 32768}, **extra
    ):
        if "message" in event or "result" in event:
            for kind, text in think.finish_message():
                yield AgentEvent(kind, text)
        if isinstance(event.get("data"), str):
            for kind, text in think.feed(event["data"]):
                yield AgentEvent(kind, text)
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
                _cached(usage),
            )
        elif event.get("current_tool_use", {}).get("name"):
            tool = event["current_tool_use"]
            tool_id = tool.get("toolUseId")
            if tool_id and tool_id not in seen_tools:
                seen_tools[tool_id] = tool["name"]
                yield AgentEvent("tool_started", tool["name"])
        elif isinstance(event.get("message"), dict):
            for block in event["message"].get("content", []):
                use = block.get("toolUse") if isinstance(block, dict) else None
                if isinstance(use, dict) and isinstance(use.get("name"), str):
                    yield AgentEvent("tool_call", tool_summary(use["name"], use.get("input")))
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
                extra_in, extra_out, extra_cached = ledger.drain() if ledger else (0, 0, 0)
                total_in = int(usage.get("inputTokens", 0) or 0) + extra_in
                total_out = int(usage.get("outputTokens", 0) or 0) + extra_out
                yield AgentEvent(
                    "usage",
                    f"Input tokens: {total_in}; output tokens: {total_out}",
                    total_in,
                    total_out,
                    _cached(usage) + extra_cached,
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
