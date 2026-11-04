from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal[
        "text_delta",
        "tool_started",
        "tool_result",
        "error",
        "turn_completed",
        "usage",
        "compacted",
        "reasoning",
        "call_usage",
        "tool_call",
    ]
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class SearchService(Protocol):
    def search(self, query: str) -> list[str]: ...
