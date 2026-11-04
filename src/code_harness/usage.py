"""Cumulative token and optional cost tracking for one chat process."""

import threading
from dataclasses import dataclass, field


@dataclass
class SubagentLedger:
    """One main agent's subagent token budget, unreported tokens and stop signal.

    The number of subagents is not limited. `token_budget` caps the uncached tokens they use in one
    turn (0 means no cap), which is what bounds the cost of spawning many.
    """

    token_budget: int = 0
    stop: threading.Event = field(default_factory=threading.Event)
    tokens_this_turn: int = 0
    spawned_total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def begin_turn(self) -> None:
        self.stop.clear()
        with self._lock:
            self.tokens_this_turn = 0

    def budget_used(self) -> bool:
        with self._lock:
            return bool(self.token_budget) and self.tokens_this_turn >= self.token_budget

    def reserve(self, role: str) -> str | None:
        """A display label for a new subagent, or None when the turn's tokens are used up."""
        if self.budget_used():
            return None
        with self._lock:
            self.spawned_total += 1
            return f"{role}-{self.spawned_total}"

    def add(self, input_tokens: int, output_tokens: int, cached_tokens: int) -> None:
        with self._lock:
            self.tokens_this_turn += input_tokens + output_tokens
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cached_tokens += cached_tokens

    def drain(self) -> tuple[int, int, int]:
        """Tokens not yet reported: (input, output, cached). Resets them."""
        with self._lock:
            totals = (self.input_tokens, self.output_tokens, self.cached_tokens)
            self.input_tokens = self.output_tokens = self.cached_tokens = 0
            return totals


@dataclass
class UsageTracker:
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    turns: int = 0

    def add(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> None:
        self.cached_tokens += max(0, cached_tokens)
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.turns += 1

    @property
    def cost(self) -> float | None:
        """Estimated cost; None unless the user configured both prices."""
        if self.input_price_per_mtok is None or self.output_price_per_mtok is None:
            return None
        return (
            self.input_tokens * self.input_price_per_mtok
            + self.output_tokens * self.output_price_per_mtok
        ) / 1_000_000

    def summary(self) -> str:
        text = (
            f"Session total: {self.turns} turns, {self.input_tokens} input tokens, "
            f"{self.output_tokens} output tokens, {self.cached_tokens} cached input tokens"
        )
        cost = self.cost
        if cost is None:
            return text + ". Cost unknown: set INPUT_PRICE_PER_MTOK and OUTPUT_PRICE_PER_MTOK."
        return text + f". Estimated cost: {cost:.4f} (user-configured prices; not a bill)."
