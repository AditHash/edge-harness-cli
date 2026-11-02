from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["text_delta", "tool_started", "tool_result", "error", "turn_completed", "usage"]
    text: str = ""


class SearchService(Protocol):
    def search(self, query: str) -> list[str]: ...
