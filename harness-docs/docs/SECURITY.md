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

## Cross-platform notes (2026-11-02)

- **Sandbox backends**: Windows uses WSL Bubblewrap; Linux uses native Bubblewrap with the same
  supervisor script; macOS has no backend and command execution and local MCP are disabled with a
  stated reason. There is no unsandboxed fallback.
- **POSIX edits**: every path component is opened with `O_NOFOLLOW|O_DIRECTORY` from the workspace
  root; targets need `O_NOFOLLOW`, regular file, link count 1 and an exclusive `flock`; deletion
  re-checks device/inode before `unlink`. Tests cover symlinked-parent swaps and hard links.
- **Remote MCP**: HTTPS only, no credentials/headers/query, public destinations only, same-origin
  redirects, per-call approval. Arguments you approve leave the machine. Residual risk: DNS can change
  between validation and connect.
- **Retrieval** is lexical and local; it reads through `Workspace`, so protected files are unreachable.
- **Usage/cost** figures are estimates from user-supplied prices.

## Session approvals (2026-11-03)

Typing `all` at an approval prompt grants a standing approval for one chat process, separately
for file edits and for sandbox commands. Every later change is still validated (paths, protected
files, stale-diff and control-character checks) and its diff is still displayed; only the reply is
skipped. It is reset when a chat starts and by `/approve ask`, is never granted when stdin is not a
terminal, and never covers MCP calls. Commands under a standing approval still run only in the
sandbox. Model and repository text is rendered without Rich markup so it cannot fake prompts.

## Images, global skills and saved MCP servers (2026-11-04)

- **Images:** `/image` is typed by the user and has no tool form, so the model cannot request a
  file this way. The path may be outside the workspace, but the file must be a regular file (no
  link) with a PNG, JPEG, GIF or WebP header, at most 3.75 MB; other content is refused and never
  sent. Images are stored unencrypted in the session database.
- **Global skills:** read from `<state>/skills` through the same `Workspace` checks. Skill text is
  labelled untrusted guidance and cannot grant tools or bypass approvals.
- **Saved MCP servers:** stored only in `<state>/mcp-servers.json`, and only when the user runs
  `--mcp-add`; repository files cannot add one. Fields are re-validated on every load, credential
  fields are refused, and every connection and tool call still needs approval.
- **Session titles** hold the first 60 characters of the first message, control characters removed.

## Subagents (2026-11-04)

- `spawn_subagent` builds a fresh agent per call with no parent conversation and least-privilege
  tools: `explore` and `review` are read-only, `test` adds the sandbox, `edit` adds edits and the
  sandbox. A role whose capability is unavailable is not offered.
- Subagents never get `spawn_subagent` (depth one) or MCP tools, because the MCP approval guard is a
  hook on the main agent. No subagent has host command access, and only `explore` can reach the
  network, through the approved `web_fetch`.
- Edits and commands use the main agent's guarded tools and its approval lock, so a subagent cannot
  skip an approval. `approve-all` covers them exactly as it covers the main agent.
- Limits: the number of subagents is not capped. Up to `HARNESS_SUBAGENT_MAX_PARALLEL` run at once
  and new spawns stop once `HARNESS_SUBAGENT_TURN_TOKENS` uncached tokens are used in a turn; running
  subagents finish, so a turn can pass that figure. Each has the main agent's turn and output limits
  and a 20,000 character report. Provider errors are reduced to the exception type. Reports are marked untrusted.

## Web fetch (2026-11-04)

- The SDK's built-in `web_fetch` is not used: it has no block on private addresses, so a
  prompt-injected model could reach a cloud metadata service or the LAN, including through a redirect.
- `web_fetch` allows http and https only, refuses credentials in URLs, and requires every resolved
  address to be public. Each redirect hop is checked again, and the connection goes to the checked
  address, so a DNS change cannot move it. Text responses only, at most 5 MB and 50,000 characters,
  30 seconds. No cookies or credentials are sent.
- There is no web search tool, so no search provider receives queries.
- Every fetch needs approval and is refused without a terminal. Standing approvals for edits and
  commands do not cover it. Typing `a` covers one host for the session.
- **Remaining risk:** a URL can carry repository content out, and a host approved with `a`
  is not asked again, although each URL is still shown. Read URLs with long query strings before
  approving. Web content is marked untrusted, but a model can still be misled by it.

## Commands on the real machine (2026-11-04)

- `run_on_host` runs one command outside the sandbox with the user's files, logins and network, for
  work like `git push`. It cannot be sandboxed, so the user's review is the protection.
- Each command needs a typed `yes`. `y`, `a` and standing approvals do not count, and it is refused
  without a terminal. Subagents never receive it.
- Refused before review: several lines, control or hidden characters, and over 4,000 characters.
- Environment variables named like secrets (`AWS_*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`
  and similar) are removed. This is partial: a command can still read credential files and use
  stored logins, which is how `git push` works. A login held only in such a variable, like
  `GH_TOKEN`, will not reach the command.
- Stdin is closed, Git prompts are off, and it stops after 300 seconds or on cancel, process tree
  included. The real exit status is printed for the user. Output of up to 20,000 characters goes to
  the model, and so to Bedrock.
- **Remaining risk:** a harmful command the user approves does real, possibly irreversible damage, and
  a prompt-injected model may propose one. Read every command before typing `yes`.

## Settings and prices (2026-11-05)

- Settings are read only from the user's own state folder, so a repository cannot change them. Each
  saved value is checked against a fixed list of keys, a format and a range. Unknown or damaged
  entries are ignored, and no field holds a credential.
- A setting from an environment variable wins and is read-only on the page.
- The settings page changes only what the user submits. It can turn `run_on_host` and `web_fetch` off
  or on; turning them on does not weaken the approval each call needs.
- `--refresh-prices` makes a read-only, free call to the AWS Price List API and sends no repository
  content. A damaged price file falls back to the snapshot or to unknown, never to a wrong rate.
