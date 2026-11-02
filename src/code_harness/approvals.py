"""Fail-closed review of immutable proposals at the user interface boundary."""

from collections.abc import Callable
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


def review_proposal(
    proposal: Proposal,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
) -> bool:
    """Show the exact diff and require an affirmative decision for this proposal.

    A non-interactive caller is never allowed to grant approval.
    """
    validate_reviewable(proposal)
    if not interactive:
        return False
    try:
        display(f"Proposed change: {proposal.path}\n{proposal.diff}")
        return (
            read_reply("Apply this exact change? Type yes to approve: ").strip().casefold() == "yes"
        )
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_command(
    command: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
) -> bool:
    """Request separate approval for an isolated command."""
    if not interactive:
        return False
    try:
        display(
            "Proposed sandbox command (copied workspace, no network):\n"
            f"{command}\nSandbox changes do not modify the live workspace."
        )
        return (
            read_reply("Run this exact command? Type yes to approve: ").strip().casefold() == "yes"
        )
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_mcp(
    action: str,
    details: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
) -> bool:
    """Review a sandboxed MCP server launch or exact tool call."""
    if not interactive:
        return False
    try:
        display(
            f"MCP {action}:\n{details}\nServer runs in a copied workspace without network access."
        )
        return read_reply("Approve this MCP action? Type yes: ").strip().casefold() == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False
