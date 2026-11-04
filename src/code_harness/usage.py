"""Cumulative token and optional cost tracking for one chat process."""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from code_harness.pricing import format_usd


@dataclass
class SubagentLedger:
    """One main agent's subagent token budget, unreported tokens and stop signal.

    The number of subagents is not limited. `token_budget` caps the uncached tokens they use in one
    turn (0 means no cap), which is what bounds the cost of spawning many.
    """

    token_budget: int = 0
    # Called with (model, input, output, cached) for each subagent call as it finishes.
    listener: Callable[[str, int, int, int], Any] | None = None
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

    def add(
        self, input_tokens: int, output_tokens: int, cached_tokens: int, model: str = ""
    ) -> None:
        if self.listener is not None:
            self.listener(model, input_tokens, output_tokens, cached_tokens)
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
    """Token and cost totals for the current session.

    Costs are estimates from `prices` (see pricing.py), never a bill. A call to a model with no known
    price adds nothing and is counted in `unpriced_calls`, so a total is never silently too low.
    """

    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    turns: int = 0
    prices: Any = None
    budget_usd: float | None = None
    spent: float = 0.0
    calls: int = 0
    unpriced_calls: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)
    _warned: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> None:
        self.cached_tokens += max(0, cached_tokens)
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.turns += 1

    def add_call(
        self, model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
    ) -> float | None:
        """Price one model call as it finishes; safe from subagent threads. None if unpriced."""
        price = self.prices.get(model) if self.prices is not None and model else None
        amount = price.cost(input_tokens, output_tokens, cached_tokens) if price else None
        with self._lock:
            self.calls += 1
            entry = self.by_model.setdefault(
                model or "unknown", {"input": 0, "output": 0, "cached": 0, "cost": 0.0}
            )
            entry["input"] += input_tokens
            entry["output"] += output_tokens
            entry["cached"] += cached_tokens
            if amount is None:
                self.unpriced_calls += 1
            else:
                self.spent += amount
                entry["cost"] += amount
        return amount

    @property
    def cost(self) -> float | None:
        """Estimated cost so far; None when nothing could be priced."""
        if self.calls:
            return None if self.unpriced_calls == self.calls else self.spent
        if self.input_price_per_mtok is None or self.output_price_per_mtok is None:
            return None
        return (
            self.input_tokens * self.input_price_per_mtok
            + self.output_tokens * self.output_price_per_mtok
        ) / 1_000_000

    def budget_state(self) -> str:
        """`over`, `near` (80% or more) or empty, against the optional session budget."""
        if not self.budget_usd or self.cost is None:
            return ""
        if self.cost >= self.budget_usd:
            return "over"
        return "near" if self.cost >= 0.8 * self.budget_usd else ""

    def budget_warning(self) -> str:
        """A message the first time each budget level is reached in this session."""
        state = self.budget_state()
        if not state or state == self._warned or (state == "near" and self._warned == "over"):
            return ""
        self._warned = state
        word = "is over" if state == "over" else "is near"
        return (
            f"Estimated session cost {format_usd(self.cost or 0)} {word} your budget of "
            f"{format_usd(self.budget_usd or 0)}. Nothing was stopped."
        )

    def totals(self) -> dict[str, Any]:
        """What a session saves, so its cost carries across a resume."""
        with self._lock:
            return {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cached": self.cached_tokens,
                "spent": self.spent,
                "calls": self.calls,
                "unpriced": self.unpriced_calls,
                "models": {name: dict(entry) for name, entry in self.by_model.items()},
            }

    def start_session(self, saved: Any = None, turns: int = 0) -> None:
        """Switch to a session's totals; damaged saved values count as zero."""
        data = saved if isinstance(saved, dict) else {}

        def number(key: str) -> float:
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
                return 0.0
            return float(value)

        models = data.get("models")
        with self._lock:
            self.input_tokens = int(number("input"))
            self.output_tokens = int(number("output"))
            self.cached_tokens = int(number("cached"))
            self.spent = number("spent")
            self.calls = int(number("calls"))
            self.unpriced_calls = int(number("unpriced"))
            self.turns = turns
            self._warned = ""
            self.by_model = {}
            for name, entry in models.items() if isinstance(models, dict) else ():
                if isinstance(name, str) and isinstance(entry, dict):
                    self.by_model[name] = {
                        key: float(entry[key])
                        for key in ("input", "output", "cached", "cost")
                        if isinstance(entry.get(key), int | float)
                        and not isinstance(entry.get(key), bool)
                        and entry[key] >= 0
                    }
                    for key in ("input", "output", "cached", "cost"):
                        self.by_model[name].setdefault(key, 0.0)

    def summary(self) -> str:
        text = (
            f"Session total: {self.turns} turns, {self.input_tokens} input tokens, "
            f"{self.output_tokens} output tokens, {self.cached_tokens} cached input tokens"
        )
        cost = self.cost
        if cost is None:
            return text + ". Cost unknown: no price for this model. Set prices in /settings."
        note = (
            f" {self.unpriced_calls} calls had no price and are not included."
            if (self.unpriced_calls)
            else ""
        )
        return text + f". Estimated cost: {format_usd(cost)} (an estimate, not a bill).{note}"
