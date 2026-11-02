"""Fail-closed review of immutable proposals at the user interface boundary."""

from collections.abc import Callable
from unicodedata import category

from code_harness.proposals import Proposal
from code_harness.workspace import WorkspaceError


def review_proposal(
    proposal: Proposal,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
) -> bool:
    """Show the exact diff and require an affirmative decision for this proposal.

    A non-interactive caller is never allowed to grant approval. Escape/control
    sequences are rejected instead of being allowed to manipulate the terminal.
    """
    if any(
        category(char).startswith("C") and char not in "\n\r\t" for char in proposal.diff
    ) or "\r" in proposal.diff.replace("\r\n", ""):
        raise WorkspaceError(
            "Diff contains terminal control characters and cannot be reviewed safely"
        )
    if not interactive:
        return False
    try:
        display(f"Proposed change: {proposal.path}\n{proposal.diff}")
        return (
            read_reply("Apply this exact change? Type yes to approve: ").strip().casefold() == "yes"
        )
    except (EOFError, KeyboardInterrupt, OSError):
        return False
