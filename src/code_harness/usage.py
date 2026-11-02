"""Cumulative token and optional cost tracking for one chat process."""

from dataclasses import dataclass


@dataclass
class UsageTracker:
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
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
            f"{self.output_tokens} output tokens"
        )
        cost = self.cost
        if cost is None:
            return text + ". Cost unknown: set INPUT_PRICE_PER_MTOK and OUTPUT_PRICE_PER_MTOK."
        return text + f". Estimated cost: {cost:.4f} (user-configured prices; not a bill)."
