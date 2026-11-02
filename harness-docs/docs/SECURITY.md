# SECURITY.md — Threat model and release gates

This is a local tool that reads and potentially modifies developers' repositories. Treat **model output, repository text, MCP tools, and subprocess output as untrusted**. A system prompt and a filesystem permission rule are not complete security boundaries.

## Trust boundaries

1. **User → CLI:** user selects one explicit workspace, model, and permissions.
2. **Model → tools:** arguments are validated, policy-checked, audited and possibly approved. A model cannot grant itself extra capabilities.
3. **Repository → model:** source files and documentation may contain prompt injection; never let them override tool policies or system instructions.
4. **Agent → execution:** commands run only in an isolated sandbox, never in the host process.
5. **Agent → external services:** Bedrock inputs should include only user-authorized code/context. Remote MCP and telemetry require opt-in and redaction.

## Non-negotiable invariants

- Every file operation is confined to the explicit workspace after canonicalization, with symlink/junction handling and time-of-check/time-of-use considerations.
- Deny read/write of known sensitive paths by default (`.env`, `.env.*`, credentials, private keys, `.aws`, SSH material, `.git` internals). Implement policy beyond string matching and test it.
- Ignore rules such as `.gitignore` help performance but are **not** security policy; exclusions must be enforced by tools/backend.
- All untrusted file edits/deletions require explicit review. EOF, timeout, Ctrl+C and denied approvals must fail closed.
- Never execute agent-provided commands on the host (including through `LocalShellBackend` or `subprocess`).
- Isolated shell runs without credentials, Docker socket, privileged mode or unrestricted host mounts; default no network.
- Execution has hard timeouts, memory/CPU/output limits and process-tree cancellation.
- Do not print access keys, secret environment variables, private contents or raw model traces containing secrets. Redact before logs/tracing.
- Protect against file overwrites: stage a unified diff, capture original hash/mtime where meaningful, re-validate at apply, and reject conflicts.
- Return sandbox-generated edits to the host only as a separately approved patch, not as an implicit writable mount.
- Prompt injection in any repository content must not trigger forbidden tool calls, remote exfiltration or policy changes.
- MCP tool descriptions and outputs are untrusted. Each Strands custom tool, built-in tool and LangChain retrieval result requires independent policy enforcement; neither Strands interventions nor LangChain filters are OS containment.

## Permission levels

- **Read-only (default):** search, list and read non-sensitive workspace files.
- **Propose:** calculate a patch or command plan; not yet executed.
- **Approved edit:** write only the reviewed patch to allowed paths; approval is scoped to exact proposal and expires if content changes.
- **Approved sandbox execution:** run an explicitly reviewed command inside isolation; does not confer host write access.

No broad `--dangerously-skip-permissions` equivalent in the MVP.

## Forbidden default operations

`git reset --hard`, `git clean -fdx`, deleting arbitrary paths, unreviewed `git commit/push`, credential access, cloud changes, shell access to host, network data export, `curl | sh`, `sudo`, privileged Docker, and writing outside workspace.

## Security acceptance tests

- Relative traversal (`../`), absolute outside-root paths, symlinks to outside, and Windows junction equivalents are denied.
- `.env`, `.env.local`, `.ssh`, AWS credentials, and nested secrets never appear in model tool results, logs, or diffs.
- `write_file` and `edit_file` denial leaves exact bytes unchanged.
- Force an approval interruption and resume with `reject` to verify zero writes.
- Change source between proposal and approval; stale patch is rejected.
- Inject malicious instructions into a README; model cannot override tools' independent policy.
- Try model-initiated shell command before Phase 4; no execute capability exists.
- In Phase 4, verify a malicious command cannot read host home directory, run on the host, mount Docker socket or reach the internet by default.
- Terminate an executing sandbox command; no orphaned processes or partial host-side edits.
- Failed/truncated tool output is labeled as such; never silently marked success.

## Framework-specific limitations

- Strands custom tools and default host read/write/shell tools execute with local process privileges. Disable built-ins initially, register audited custom tools, and enforce filesystem authorization inside tool/service code.
- `interventions="ask"` and other approval prompts **do not** enforce OS isolation. Explicit approvals must be bound to the actual proposed action; policy still checks every call.
- LangChain retrievers and vector indexes can retain protected text even if direct file reads are blocked. Validate the indexed paths at ingest and **again** at retrieval; purge state when workspace or permissions change.
- LangGraph or Deep Agents, if later used as a scoped specialist, receive only authorized snapshots and do not get implicit access to root-agent privileges.

Official docs:
- https://strandsagents.com/docs/user-guide/harness/configure/tools-and-instructions/
- https://strandsagents.com/docs/user-guide/sdk/tools/
- https://strandsagents.com/docs/user-guide/harness/configure/interventions/

## MCP implementation notes

Local stdio servers only, started inside the WSL Bubblewrap sandbox (no network, copied filtered
workspace, cleared environment, bounded memory/time/output). Config accepts only `name` and
`command`. Startup and each tool call are approved separately by a Strands `BeforeToolCallEvent`
hook that cancels the call on denial or on any approval error; denied calls never reach the server.
Tool count, definition size and tool names are bounded. MCP output remains untrusted model input.
Remaining risk: the sandbox shares the WSL kernel and has no aggregate cgroup quota.

## Git inspection notes

`git_status`/`git_diff` run fixed argv on the host with fsmonitor, hooks, external diff, textconv
and pager disabled, protected paths excluded by pathspec, and a 64 KiB output cap. A repository
owned by another OS account is refused by Git (`safe.directory`); the tool reports failure instead
of overriding that check.
