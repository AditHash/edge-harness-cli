"""Checks on commands the model proposes to run on the real machine.

These help the user notice danger; they are not a security boundary. A shell has endless ways to
write the same thing, so a command with no warning is not therefore safe, and the user's review
remains the real protection.
"""

import re

# (pattern, what the user should know). Matched anywhere in the command, including chains.
_DESTRUCTIVE = (
    (
        r"\bgit\b[^&|;]*\bpush\b[^&|;]*(?:--force\b|--force-with-lease\b|\s-f\b|\s\+\S)",
        (
            "Force-push rewrites history on the remote. Replaced commits can stay readable by id on "
            "GitHub, so this does not erase anything already published."
        ),
    ),
    (
        r"\bgit\b[^&|;]*\bpush\b[^&|;]*(?:--delete\b|\s-d\b|--mirror\b)",
        "Deletes or overwrites branches on the remote.",
    ),
    (r"\bgit\b[^&|;]*\breset\b[^&|;]*--hard\b", "Discards uncommitted work with no undo."),
    (r"\bgit\b[^&|;]*\bclean\b[^&|;]*\s-\w*f", "Permanently deletes untracked files."),
    (
        r"\bgit\b[^&|;]*\b(?:checkout\s+--|restore\b(?![^&|;]*--staged))",
        "Discards uncommitted changes to files.",
    ),
    (r"\bgit\b[^&|;]*\bbranch\b[^&|;]*\s-D\b", "Deletes a branch even if it is not merged."),
    (
        r"\bgit\b[^&|;]*\b(?:rebase|filter-repo|filter-branch)\b",
        "Rewrites commit history.",
    ),
    (r"\bgit\b[^&|;]*\bcommit\b[^&|;]*--amend\b", "Rewrites the last commit."),
    (r"\bgit\b[^&|;]*\bstash\s+(?:drop|clear)\b", "Permanently deletes stashed work."),
    (
        r"\brm\s+(?:-\w*[rf]\w*|--recursive|--force)\b",
        "Permanently deletes files; there is no recycle bin.",
    ),
    (r"\b(?:rmdir|rd)\s+/s\b", "Permanently deletes a folder and everything in it."),
    (r"\bdel\s+", "Permanently deletes files; there is no recycle bin."),
    (r"\bRemove-Item\b", "Permanently deletes files; there is no recycle bin."),
    (r"\b(?:format|diskpart|mkfs\S*)\b(?:\s|$)", "Can erase a disk."),
    (r"\bdd\s[^&|;]*\bof=", "Can overwrite a disk or file."),
    (r"\breg\s+delete\b", "Deletes Windows registry entries."),
    (r"\b(?:shutdown|taskkill)\b", "Stops the machine or running programs."),
    (
        r"\b(?:curl|wget|iwr|Invoke-WebRequest)\b[^&;]*\|\s*(?:\w*sh|iex|Invoke-Expression|python\S*)\b",
        "Downloads a program from the internet and runs it unseen.",
    ),
    (r"\b(?:iex|Invoke-Expression)\b", "Runs text as a program."),
    (r"\bpowershell\S*\s[^&|;]*-e(?:nc\w*)?\s", "Runs a hidden, encoded command."),
    (
        r"\bgh\s+repo\s+(?:delete|edit\b[^&|;]*--visibility)",
        "Deletes a GitHub repository or changes who can see it.",
    ),
    (
        r"\baws\s[^&|;]*\b(?:delete|terminate|remove|rm|rb|destroy)\S*",
        "Deletes or stops AWS resources.",
    ),
    (r"\bterraform\s+destroy\b", "Deletes cloud infrastructure."),
    (r"\bkubectl\s+delete\b", "Deletes cluster resources."),
    (r"\bdocker\s+(?:system\s+prune|volume\s+rm)\b", "Permanently deletes Docker data."),
)
_READERS = (
    r"(?:cat|type|more|less|head|tail|gc|Get-Content|findstr|grep|rg|sls|Select-String|strings"
    r"|xxd|certutil|base64|copy|cp|scp|curl\s+[^&|;]*-T|Invoke-WebRequest)"
)
_SECRET_FILES = (
    r"(?:\.env(?![.\w-]*(?:example|sample|template))\b|\.aws[/\\]|\.ssh[/\\]|id_rsa|id_ed25519"
    r"|\.netrc|\.pgpass|credentials(?:\.json)?\b|\.pem\b|\.pfx\b|\.p12\b)"
)
# Refused outright: they exist to print a credential, and the output goes to the model.
_SECRET_READS = (
    rf"\b{_READERS}\b[^&|;]*{_SECRET_FILES}",
    r"\bgh\s+auth\s+(?:token|status\b[^&|;]*--show-token)",
    (
        r"\baws\s+(?:configure\s+(?:export-credentials|get\b[^&|;]*(?:secret|token))"
        r"|sts\s+get-session-token)"
    ),
    r"\bgit\s+credential\s+fill\b",
    r"\bgit\s+config\b[^&|;]*(?:token|password)",
    r"(?:^|[&|;]\s*)(?:printenv|env|set|Get-ChildItem\s+env:|gci\s+env:|dir\s+env:)\s*(?:$|[&|;])",
    r"\bcmdkey\s+/list\b",
    r"\bsecurity\s+find-\w+-password\b",
)
_SECRET_VALUES = (
    (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
        "private key",
    ),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "AWS key id"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "GitHub token"),
    (r"\bsk-[A-Za-z0-9_-]{20,}\b", "API key"),
    (r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b", "Slack token"),
    (
        (
            r"(?i)\b(aws_secret_access_key|aws_session_token|api[_-]?key|password|passwd|secret|token)"
            r"(\s*[=:]\s*)[\"']?[^\s\"']{8,}"
        ),
        "secret value",
    ),
    (r"(https?://)[^/\s:@]+:[^/\s@]+@", "password in a URL"),
)


def command_risks(command: str) -> list[str]:
    """What could go badly wrong with this command, for the user; empty does not mean safe."""
    # A commit message is only words. Other quoted text stays, because `sh -c "rm -rf x"` is real.
    checked = re.sub(r"""(\bcommit\b[^&|;]*?\s-m\s+)("[^"]*"|'[^']*')""", r'\1""', command)
    found: list[str] = []
    for pattern, reason in _DESTRUCTIVE:
        if re.search(pattern, checked) and reason not in found:
            found.append(reason)
    return found


def reads_secret(command: str) -> bool:
    """True for a command whose purpose is to reveal a credential to the model."""
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in _SECRET_READS)


def redact_secrets(text: str) -> str:
    """Remove recognizable credentials from output before anything else reads it."""
    for pattern, label in _SECRET_VALUES:
        if label == "secret value":
            text = re.sub(pattern, rf"\1\2[{label} removed]", text)
        elif label == "password in a URL":
            text = re.sub(pattern, rf"\1[{label} removed]@", text)
        else:
            text = re.sub(pattern, f"[{label} removed]", text, flags=re.DOTALL)
    return text
