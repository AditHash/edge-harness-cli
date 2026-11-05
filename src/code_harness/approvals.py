"""Fail-closed review of immutable proposals at the user interface boundary."""

from collections.abc import Callable
from dataclasses import dataclass, field
from unicodedata import category
from urllib.parse import urlsplit

from code_harness.guardrails import command_risks
from code_harness.proposals import Proposal
from code_harness.workspace import WorkspaceError

HOST_PREFIX = "Proposed command on your REAL machine:"
HOST_WARNING = "WARNING:"
BROWSER_PREFIX = "Proposed browser action:"
MAX_HOST_PROMPTS = 3


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

    Edits, sandbox commands and each fetched host are tracked separately, so
    approving one never approves another. MCP calls are never covered.
    """

    edits: bool = False
    commands: bool = False
    web_hosts: set[str] = field(default_factory=set)
    browser_hosts: set[str] = field(default_factory=set)

    def reset(self) -> None:
        self.edits = False
        self.commands = False
        self.web_hosts.clear()
        self.browser_hosts.clear()

    def short_label(self) -> str:
        """For the status bar. Names what is automatic, because real machine commands never are."""
        automatic = [name for name, on in (("edits", self.edits), ("sandbox", self.commands)) if on]
        return " + ".join(automatic) + " auto" if automatic else "ask"

    def describe(self) -> str:
        edits = "approved for this session" if self.edits else "ask each time"
        commands = "approved for this session" if self.commands else "ask each time"
        hosts = ", ".join(sorted(self.web_hosts)) or "none"
        return (
            f"File changes: {edits}. Sandbox commands: {commands}. "
            f"Web pages approved from: {hosts}. MCP calls: ask each time. "
            "Real machine commands: type yes each time."
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


def review_fetch(
    url: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    session: SessionApprovals | None = None,
    in_browser: bool = False,
) -> bool:
    """Approve one page fetch or browser visit. A standing approval covers one host only."""
    if not interactive:
        return False
    host = urlsplit(url).hostname or ""
    try:
        kind = "browser visit" if in_browser else "web fetch"
        display(f"Proposed {kind} (this site receives the full URL):\n{url}")
        if session is not None and host in session.web_hosts:
            display("Fetched under your session approval (/approve ask to stop).")
            return True
        choices = f"[y]es / [a]ll from {host} this session / [n]o" if session else "[y]es / [n]o"
        question = "Open this page in the browser?" if in_browser else "Fetch this page?"
        decision = _decide(read_reply(f"{question} {choices}: "))
        if decision == "all" and session is not None:
            session.web_hosts.add(host)
            return True
        return decision == "yes"
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_host(
    command: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    preview: str = "",
) -> bool:
    """Approve one command on the real machine. Only a typed word counts, every time.

    A command with known dangers needs `confirm`, so a habitual `yes` cannot approve it.
    """
    if not interactive:
        return False
    risks = command_risks(command)
    word = "confirm" if risks else "yes"
    try:
        warning = "".join(f"{HOST_WARNING} {risk}\n" for risk in risks)
        details = f"{preview}\n" if preview else ""
        display(
            f"{HOST_PREFIX}\n{command}\n{details}{warning}"
            "NOT sandboxed: it runs with your files, logins and network, and the model reads its "
            "output. Check it did not come from repository or web content."
        )
        # A slip like `y` asks again instead of silently rejecting; it never approves.
        for _ in range(MAX_HOST_PROMPTS):
            reply = read_reply(f"Run on your REAL machine? Type {word}, or no to reject: ")
            answer = reply.strip().casefold()
            if answer == word:
                return True
            if answer in {"", "n", "no"}:
                break
            display(f"Not approved yet: type the full word {word}, or no to reject.")
        display("Rejected. The command did not run.")
        return False
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def review_browser_action(
    summary: str,
    host: str,
    *,
    interactive: bool,
    display: Callable[[str], None],
    read_reply: Callable[[str], str],
    session: SessionApprovals | None = None,
) -> bool:
    """Approve one click, typed text or key press.

    Standing approval exists only for the user's own local app. On a public site a page can steer
    the model, so every action there is asked about.
    """
    if not interactive:
        return False
    local = host in {"localhost", "127.0.0.1", "::1"}
    try:
        display(f"{BROWSER_PREFIX}\n{summary}")
        if session is not None and host in session.browser_hosts:
            display("Done under your session approval (/approve ask to stop).")
            return True
        offer_all = local and session is not None
        choices = f"[y]es / [a]ll on {host} this session / [n]o" if offer_all else "[y]es / [n]o"
        decision = _decide(read_reply(f"Do this in the browser? {choices}: "))
        if decision == "all" and offer_all and session is not None:
            session.browser_hosts.add(host)
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
