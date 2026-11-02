"""Fail-closed review of immutable proposals at the user interface boundary."""

from collections.abc import Callable
from dataclasses import dataclass
from unicodedata import category

from code_harness.proposals import Proposal
from code_harness.workspace import WorkspaceError


def validate_reviewable(proposal: Proposal) -> None:
    """Reject controls that could falsify the diff shown in a terminal."""
    if any(
        category(char).startswith("C") and char not in "\n\r\t" for char in proposal.diff
    ) or "\r" in proposal.diff.replace("\r\n", ""):
        raise WorkspaceError(
            "Diff contains terminal control characters and cannot be reviewed safely"
        )


@dataclass
class SessionApprovals:
    """Standing approvals the user granted by typing `all`; they last for one chat session.

    Edits and sandbox commands are tracked separately. MCP calls are never covered.
    """

    edits: bool = False
    commands: bool = False

    def reset(self) -> None:
        self.edits = False
        self.commands = False

    def describe(self) -> str:
        edits = "approved for this session" if self.edits else "ask each time"
        commands = "approved for this session" if self.commands else "ask each time"
        return f"File changes: {edits}. Sandbox commands: {commands}. MCP calls: ask each time."


def _decide(reply: str) -> str:
    answer = reply.strip().casefold()
    return answer if answer in {"yes", "all"} else "no"


def review_proposal(
    proposal: Proposal,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    session: SessionApprovals | None = None,
) -> bool:
    """Show the exact diff and require an affirmative decision for this proposal.

    A non-interactive caller is never allowed to grant approval. With a standing session
    approval the diff is still validated and shown, but no reply is needed.
    """
    validate_reviewable(proposal)
    if not interactive:
        return False
    try:
        display(f"Proposed change: {proposal.path}\n{proposal.diff}")
        if session is not None and session.edits:
            display("Applied under your session approval (/approve ask to stop).")
            return True
        prompt = "Apply this exact change? Type yes to approve"
        if session is not None:
            prompt += ", all to approve every change this session"
        decision = _decide(read_reply(prompt + ": "))
        if decision == "all" and session is not None:
            session.edits = True
            return True
        return decision == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_command(
    command: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    session: SessionApprovals | None = None,
) -> bool:
    """Request separate approval for an isolated command."""
    if not interactive:
        return False
    try:
        display(
            "Proposed sandbox command (copied workspace, no network):\n"
            f"{command}\nSandbox changes do not modify the live workspace."
        )
        if session is not None and session.commands:
            display("Run under your session approval (/approve ask to stop).")
            return True
        prompt = "Run this exact command? Type yes to approve"
        if session is not None:
            prompt += ", all to approve every command this session"
        decision = _decide(read_reply(prompt + ": "))
        if decision == "all" and session is not None:
            session.commands = True
            return True
        return decision == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_mcp(
    action: str,
    details: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    note: str = "Server runs in a copied workspace without network access.",
) -> bool:
    """Review a sandboxed MCP server launch or exact tool call."""
    if not interactive:
        return False
    try:
        display(f"MCP {action}:\n{details}\n{note}")
        return read_reply("Approve this MCP action? Type yes: ").strip().casefold() == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False
