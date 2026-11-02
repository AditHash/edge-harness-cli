"""Validated, side-effect-free task plan stored in the agent's session state."""

from typing import Any

STATUSES = ("pending", "in_progress", "completed")
MAX_STEPS = 20
MAX_STEP_CHARS = 200
MARKS = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}


def validate_plan(steps: Any) -> list[dict[str, str]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError(f"a plan needs 1 to {MAX_STEPS} steps")
    plan: list[dict[str, str]] = []
    for item in steps:
        if not isinstance(item, dict) or set(item) != {"step", "status"}:
            raise ValueError("each step needs exactly `step` and `status`")
        text, status = item["step"], item["status"]
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_STEP_CHARS:
            raise ValueError("step text must be 1-200 characters")
        if any(ord(ch) < 32 for ch in text):
            raise ValueError("step text contains control characters")
        if status not in STATUSES:
            raise ValueError("status must be pending, in_progress or completed")
        plan.append({"step": text.strip(), "status": status})
    if sum(item["status"] == "in_progress" for item in plan) > 1:
        raise ValueError("only one step may be in_progress")
    return plan


def render_plan(plan: list[dict[str, str]]) -> str:
    return "\n".join(f"{MARKS[item['status']]} {item['step']}" for item in plan)
