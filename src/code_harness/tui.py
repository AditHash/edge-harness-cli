"""Textual front end over the same Strands agent, event stream and approval policy as the CLI.

Approvals are modal and fail closed: anything other than the literal word ``yes`` rejects.
Agent turns run in a worker thread so blocking tool callbacks can wait for the modal.
"""

import asyncio
from collections.abc import Callable
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, RichLog, Static

from code_harness.pricing import format_usd
from code_harness.streaming import stream_turn
from code_harness.ui import history_items
from code_harness.usage import UsageTracker


class ApprovalScreen(ModalScreen[bool]):  # type: ignore[type-var]
    """Show the exact text under review; approve only on the literal reply ``yes``."""

    BINDINGS: ClassVar[list[Any]] = [("escape", "reject", "Reject")]

    def __init__(self, prompt: str, shown: str) -> None:
        super().__init__()
        self.prompt_text = prompt
        self.shown = shown

    def compose(self) -> ComposeResult:
        with Vertical(id="approval"):
            yield Label(self.prompt_text)
            yield RichLog(id="review-text", markup=False, highlight=False, wrap=True)
            yield Input(placeholder="Type yes to approve; anything else rejects", id="reply")

    def on_mount(self) -> None:
        self.query_one("#review-text", RichLog).write(self.shown)
        self.query_one("#reply", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip().casefold() == "yes")

    def action_reject(self) -> None:
        self.dismiss(False)


class HarnessApp(App[None]):
    CSS = """
    #approval { width: 90%; height: 80%; border: thick $warning; background: $surface; padding: 1; }
    #review-text { height: 1fr; }
    #status { height: 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[Any]] = [("ctrl+c", "quit", "Quit")]

    def __init__(
        self,
        make_agent: Callable[[Callable[[str], None], Callable[[str], str]], Any],
        tracker: UsageTracker | None = None,
        title: str = "Edge harness",
        on_turn: Callable[[str], None] | None = None,
        on_usage: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.title = title
        self.tracker = tracker or UsageTracker()
        self.on_turn = on_turn
        self.on_usage = on_usage
        self._shown: list[str] = []
        self.agent = make_agent(self._collect_review, self._approval_reply)
        self._busy = False

    def on_mount(self) -> None:
        items, hidden = history_items(getattr(self.agent, "messages", None))
        if items:
            self.log_line(
                "Earlier in this session" + (f" ({hidden} rows not shown)" if hidden else "")
            )
        labels = {"user": "You > ", "assistant": "", "tool": "Tool: "}
        for kind, text in items:
            self.log_line(labels[kind] + text)

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", markup=False, highlight=False, wrap=True)
        yield Static("Ready. Edits and commands need your approval.", id="status")
        yield Input(placeholder="Message (Enter to send, /usage for totals)", id="prompt")
        yield Footer()

    # -- called from the worker thread by approval callbacks ---------------------------------
    def _collect_review(self, text: str) -> None:
        self._shown.append(text)

    def _approval_reply(self, prompt: str) -> str:
        shown = "\n".join(self._shown)
        self._shown.clear()
        approved: bool = self.call_from_thread(self._ask, prompt, shown)  # type: ignore[arg-type]
        return "yes" if approved else "no"

    async def _ask(self, prompt: str, shown: str) -> bool:
        return bool(await self.push_screen_wait(ApprovalScreen(prompt, shown)))  # type: ignore[arg-type]

    # -- UI events ---------------------------------------------------------------------------
    def log_line(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text in {"/exit", "/quit"}:
            self.exit()
        elif text in {"/usage", "/cost"}:
            self.log_line(self.tracker.summary())
        elif text.startswith("/"):
            self.log_line(
                "Commands in the TUI: /usage, /cost, /exit. Use `harness chat` for the rest."
            )
        elif self._busy:
            self.log_line("A turn is still running.")
        else:
            self._busy = True
            self.log_line(f"You > {text}")
            self.set_status("Working...")
            self.run_turn(text)

    @work(thread=True, exclusive=True)
    def run_turn(self, prompt: str) -> None:
        ledger = getattr(self.agent, "subagents", None)
        if ledger is not None:
            ledger.listener = self.tracker.add_call

        async def go() -> None:
            buffer: list[str] = []
            thinking: list[str] = []

            def flush_thinking() -> None:
                if thinking:
                    text = "".join(thinking)
                    self.call_from_thread(
                        self.log_line, f"Thinking (~{len(text) // 4:,} tokens):\n{text}"
                    )
                    thinking.clear()

            async for event in stream_turn(self.agent, prompt):
                if event.kind == "reasoning":
                    thinking.append(event.text)
                    size = sum(len(part) for part in thinking)
                    self.call_from_thread(self.set_status, f"Thinking... ~{size // 4:,} tokens")
                    continue
                flush_thinking()
                if event.kind == "text_delta":
                    buffer.append(event.text)
                    continue
                if event.kind == "call_usage":
                    self.tracker.add_call(
                        event.text, event.input_tokens, event.output_tokens, event.cached_tokens
                    )
                    cost = self.tracker.cost
                    spent = "" if cost is None else f" - ~{format_usd(cost)} this session"
                    self.call_from_thread(
                        self.set_status,
                        f"Last model call: {event.input_tokens:,} in / "
                        f"{event.output_tokens:,} out{spent}",
                    )
                    continue
                if buffer:
                    self.call_from_thread(self.log_line, "".join(buffer))
                    buffer.clear()
                if event.kind == "usage":
                    self.tracker.add(event.input_tokens, event.output_tokens, event.cached_tokens)
                self.call_from_thread(self.log_line, f"[{event.kind}] {event.text}".rstrip())
            flush_thinking()
            if buffer:
                self.call_from_thread(self.log_line, "".join(buffer))

        try:
            asyncio.run(go())
            if self.on_turn is not None:
                self.on_turn(prompt)
        except Exception as exc:  # noqa: BLE001 - provider errors may contain sensitive data
            self.call_from_thread(self.log_line, f"Turn failed ({type(exc).__name__}).")
        finally:
            if self.on_usage is not None and self.tracker.calls:
                self.on_usage()
            self._busy = False
            self.call_from_thread(self.set_status, "Ready.")
