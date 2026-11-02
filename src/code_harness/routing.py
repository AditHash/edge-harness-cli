"""`auto` as a routed model: every request goes to the best available candidate, with failover.

Candidates are ordered by residency tier (India first, then Mumbai) and context window. Each model
call tries the current best candidate; if it fails *before producing any output* with a
recoverable error (no access, throttling, service unavailable, context overflow), the call is
retried on the next candidate. Output already streamed is never mixed across models: a failure
after the first event is raised unchanged. Access errors remove a candidate for the session;
throttling and outages put it on a short cooldown. Only ``in.*`` and Mumbai models are candidates,
so failover never leaves the selected residency.
"""

import time
from collections.abc import AsyncGenerator, AsyncIterable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel
from strands.models import Model
from strands.types.exceptions import ContextWindowOverflowException, ModelThrottledException

T = TypeVar("T", bound=BaseModel)

COOLDOWN_SECONDS = 120
_RECOVERABLE_NAMES = {
    "AccessDeniedException",
    "ResourceNotFoundException",
    "ServiceUnavailableException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "InternalServerException",
    "ThrottlingException",
}


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code
    return type(exc).__name__


def classify(exc: BaseException) -> str | None:
    """'drop' (never retry this model), 'cooldown', 'bigger' (needs more context) or None."""
    if isinstance(exc, ContextWindowOverflowException):
        return "bigger"
    if isinstance(exc, ModelThrottledException):
        return "cooldown"
    code = _error_code(exc)
    if code in {"AccessDeniedException", "ResourceNotFoundException"}:
        return "drop"
    if code in _RECOVERABLE_NAMES:
        return "cooldown"
    if (
        "not available for this account" in str(exc)
        or "access" in str(exc).lower()
        and ("denied" in str(exc).lower())
    ):
        return "drop"
    return None


class RoutedModel(Model):
    """A Strands model that routes each call across ordered candidates."""

    def __init__(
        self,
        candidates: list[tuple[str, Model, int | None]],
        *,
        notify: Callable[[str], None] = lambda text: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not candidates:
            raise ValueError("auto routing needs at least one candidate model")
        self.candidates = candidates  # (model_id, model, context window)
        self.notify = notify
        self.clock = clock
        self.dropped: set[str] = set()
        self.cooldown_until: dict[str, float] = {}
        self.active_id = candidates[0][0]
        self._announced: str | None = None

    # -- routing ---------------------------------------------------------------------------
    def _available(self, minimum_context: int = 0) -> list[tuple[str, Model, int | None]]:
        now = self.clock()
        ready = [
            item
            for item in self.candidates
            if item[0] not in self.dropped
            and self.cooldown_until.get(item[0], 0) <= now
            and (item[2] or 0) >= minimum_context
        ]
        if ready:
            return ready
        # Everything cooling down: try the ones that are not permanently dropped anyway.
        return [
            item
            for item in self.candidates
            if item[0] not in self.dropped and (item[2] or 0) >= minimum_context
        ]

    async def stream(  # type: ignore[override]
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None, **kwargs: Any
    ) -> AsyncIterable[Any]:
        minimum = 0
        tried: set[str] = set()
        last_error: BaseException | None = None
        while True:
            pool = [item for item in self._available(minimum) if item[0] not in tried]
            if not pool:
                if last_error is not None:
                    raise last_error
                raise ContextWindowOverflowException("No routed model can hold this context")
            model_id, model, context = pool[0]
            tried.add(model_id)
            if model_id != self._announced:
                # Announce the first pick and every later switch.
                self.notify(f"Auto routing: using {model_id}")
                self._announced = model_id
            self.active_id = model_id
            started = False
            try:
                async for event in model.stream(messages, tool_specs, system_prompt, **kwargs):
                    started = True
                    yield event
                return
            except Exception as exc:
                action = None if started else classify(exc)
                if action is None:
                    raise
                last_error = exc
                if action == "drop":
                    self.dropped.add(model_id)
                    reason = "no access"
                elif action == "cooldown":
                    self.cooldown_until[model_id] = self.clock() + COOLDOWN_SECONDS
                    reason = "busy or unavailable"
                else:
                    minimum = (context or 0) + 1
                    reason = "context too large"
                self.notify(f"Auto routing: {model_id} skipped ({reason}); trying the next model.")

    async def structured_output(  # type: ignore[override]
        self, output_model: type[T], prompt: Any, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        model = self._current()
        async for event in model.structured_output(output_model, prompt, system_prompt, **kwargs):
            yield event

    # -- configuration and introspection delegate to the active candidate --------------------
    def _current(self) -> Model:
        for model_id, model, _ in self.candidates:
            if model_id == self.active_id:
                return model
        return self.candidates[0][1]

    def update_config(self, **model_config: Any) -> None:
        for _, model, _ in self.candidates:
            model.update_config(**model_config)

    def get_config(self) -> Any:
        config = dict(self._current().get_config())
        config["model_id"] = self.active_id
        return config

    @property
    def context_window_limit(self) -> int | None:  # type: ignore[override]
        # Compact against the smallest window still in rotation, so a failover cannot overflow.
        windows = [c for model_id, _, c in self.candidates if c and model_id not in self.dropped]
        return min(windows) if windows else None

    def estimate_utilization(self, input_tokens: int) -> float:
        limit = self.context_window_limit or 200_000
        return input_tokens / limit
