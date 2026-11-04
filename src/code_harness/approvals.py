"""Fail-closed review of immutable proposals at the user interface boundary."""

from collections.abc import Callable
from dataclasses import dataclass, field
from unicodedata import category
from urllib.parse import urlsplit

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

    Edits, sandbox commands, web searches and each fetched host are tracked separately, so
    approving one never approves another. MCP calls are never covered.
    """

    edits: bool = False
    commands: bool = False
    searches: bool = False
    web_hosts: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self.edits = False
        self.commands = False
        self.searches = False
        self.web_hosts.clear()

    def describe(self) -> str:
        edits = "approved for this session" if self.edits else "ask each time"
        commands = "approved for this session" if self.commands else "ask each time"
        searches = "approved for this session" if self.searches else "ask each time"
        hosts = ", ".join(sorted(self.web_hosts)) or "none"
        return (
            f"File changes: {edits}. Sandbox commands: {commands}. Web searches: {searches}. "
            f"Web pages approved from: {hosts}. MCP calls: ask each time."
        )


def _decide(reply: str) -> str:
    """y/yes approves once and a/all for the session; anything else, even empty, rejects."""
    answer = reply.strip().casefold()
    return {"y": "yes", "yes": "yes", "a": "all", "all": "all"}.get(answer, "no")


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
        choices = "[y]es / [a]ll this session / [n]o" if session is not None else "[y]es / [n]o"
        decision = _decide(read_reply(f"Apply to {proposal.path}? {choices}: "))
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
        choices = "[y]es / [a]ll this session / [n]o" if session is not None else "[y]es / [n]o"
        decision = _decide(read_reply(f"Run this command? {choices}: "))
        if decision == "all" and session is not None:
            session.commands = True
            return True
        return decision == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_search(
    query: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    session: SessionApprovals | None = None,
) -> bool:
    """Approve one web search; the query is sent to a third party."""
    if not interactive:
        return False
    try:
        display(f"Proposed web search (the query goes to the third-party service Exa):\n{query}")
        if session is not None and session.searches:
            display("Run under your session approval (/approve ask to stop).")
            return True
        choices = "[y]es / [a]ll searches this session / [n]o" if session else "[y]es / [n]o"
        decision = _decide(read_reply(f"Search the web? {choices}: "))
        if decision == "all" and session is not None:
            session.searches = True
            return True
        return decision == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_fetch(
    url: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    session: SessionApprovals | None = None,
) -> bool:
    """Approve one page fetch. A standing approval covers one host, never the whole web."""
    if not interactive:
        return False
    host = urlsplit(url).hostname or ""
    try:
        display(f"Proposed web fetch (this site receives the full URL):\n{url}")
        if session is not None and host in session.web_hosts:
            display("Fetched under your session approval (/approve ask to stop).")
            return True
        choices = f"[y]es / [a]ll from {host} this session / [n]o" if session else "[y]es / [n]o"
        decision = _decide(read_reply(f"Fetch this page? {choices}: "))
        if decision == "all" and session is not None:
            session.web_hosts.add(host)
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
