# Build log

This file is an **evidence log**, not a place to report intended work as complete. Codex should append dated entries after each development phase.

## Template

### YYYY-MM-DD — Phase XX: <name>

**Implemented:**
- <file/component and behavior>

**Design decisions:**
- <decision, rationale, relevant SDK versions>

**Verification actually run:**
- `<command>` — PASS / FAIL / NOT RUN (reason)

**Security checks:**
- <negative-path tests and outcomes>

**Limitations / next step:**
- <unverified behavior, blocked dependencies, next phase>

---

## Initial state

2026-11-02 — Documentation-only baseline. No application code has been implemented, run, or tested as part of this pack.

2026-11-02 — Documentation V3: provisional single-Strands-loop hybrid architecture and LangChain components described. No application code, SDK tests, lint, or live Bedrock calls run.

## 2026-11-02 — Foundation in progress

- Added a Python 3.12 project, locked dependencies, CLI diagnostics, validated workspace configuration and typed contracts.
- `uv sync --python 3.12 --dev`: passed. `uv run pytest -q`: 3 passed.
- `uv run ruff check src tests`: passed. `uv run mypy src`: passed.
- `uv run harness doctor`: passed without cloud calls. `uv run harness --help`: executed before the Typer callback correction; recheck is pending.
- Installed Strands Harness 0.3.3 and Strands Agents 1.63.0. Introspection confirms `builtin_tools`, `skills`, `memory`, and `session` are separately configurable; safe construction must explicitly disable defaults.
- Offline tool-registration verification and phase acceptance remain pending. No real Bedrock invocation was performed.

## 2026-11-02 — Read-only implementation in progress

- Added guarded workspace read/search, a deterministic LangChain Runnable behind a Strands tool, a native Bedrock model factory and streaming `ask`/`chat` commands.
- Offline registry test confirms only the three custom read tools are registered. Skills, memory, sessions and built-in tools are disabled in the current runtime.
- `uv run pytest -q`: 8 passed. `uv run mypy src`: passed. `uv run ruff check src tests`: passed after fixing CLI diagnostics. `uv run harness --help`: passed.
- Security work is incomplete: adversarial filesystem races, full tool invocation, streaming cancellation, and live model behavior require more verification. No edit, shell, MCP, or persistent-session capability is enabled yet.

## 2026-11-02 — Additional offline evidence

- A real Strands agent streamed a deterministic fake model response without network access; proposal tests cover diff generation without writes and missing final newlines.
- `uv run pytest -q`: 12 passed. `uv run ruff check src tests`: passed. `uv run mypy src`: passed.
- `docker info --format '{{.OSType}}'`: failed because the Docker Desktop Linux engine pipe is unavailable. No command-execution tool is enabled and no host execution fallback exists.
- File application and end-to-end coding tasks remain unimplemented; live Bedrock and Docker isolation tests remain unverified.

## 2026-11-02 — Streaming and edit-boundary investigation

- The Strands streaming adapter now reports tool completion without showing tool contents, token usage when supplied, and non-success stop reasons. An offline test confirms multi-turn context survives separate CLI event loops.
- Added a fail-closed diff-review function: only an explicit `yes` approves; EOF, interruption and non-interactive mode deny. Diffs containing terminal control or bidirectional formatting characters are rejected.
- Investigated Windows exclusive file and ancestor directory handles for applying edits. Tests for approved edit, stale content and recovery passed, but a negative test demonstrated that a directory could still be renamed while the attempted ancestor handles were held. A second attempt with exclusive directory sharing also failed the same test. The experimental write code was removed and is not registered with the agent; it does not meet the required workspace-boundary guarantee.
- Next: use a proven OS isolation primitive or a verified handle-relative implementation that prevents directory movement from redirecting writes. No model-initiated edits or host shell execution are enabled.
- `uv run pytest -q`: 20 passed. `uv run ruff check src tests`, `uv run ruff format --check src tests`, and `uv run mypy src`: passed after removing the unsafe experimental backend.

## 2026-11-02 — Windows approved-edit vertical slice

- A follow-up experiment established that attribute-only directory handles did not participate in the relevant Windows share check. Requesting directory-list access without delete sharing blocked directory and ancestor renames in the local test. The Windows-only existing-file editor now holds those handles and an exclusive target-file handle, rechecks the exact content digest after review, and journals original bytes before writing.
- Strands can propose an existing-file replacement, show its diff, and apply it only after an interactive `yes`. A fake model integration test exercises both approval and denial. Non-interactive input denies; terminal-control diffs, stale content, outside symlink swaps, hard links, and protected paths are rejected by the relevant layers.
- `uv run pytest -q tests/test_edits.py tests/test_approvals.py`: 9 passed. `uv run pytest -q tests/test_agent.py`: 7 passed. `uv run ruff check src tests` and `uv run mypy src`: passed during implementation.
- File creation, deletion and non-Windows editing remain disabled. The Windows implementation needs additional independent security review and broader filesystem coverage; recovery journals contain source content and inherit the user profile's OS access controls. Docker and live Bedrock remain unverified.
- After integration: `uv run pytest -q` passed all 29 tests; Ruff lint and format checks and `uv run mypy src` passed.

## 2026-11-02 — Approved file creation on Windows

- The approved-change tool can now create a file in an existing authorized directory using a create-new exclusive handle, preventing overwrite of a file created during review. A failed write marks the new file for deletion through its open handle; the journal records that the original file was absent.
- Added tests for rejection, approved creation, a racing existing file and removal of a partially written file. `uv run pytest -q`: 30 passed; Ruff lint and format and `uv run mypy src`: passed.
- File deletion and non-Windows editing remain disabled. The Windows backend still needs broader filesystem and independent security review.

## 2026-11-02 — Approved file deletion on Windows

- Added a separate deletion tool that shows the deletion diff and requires the same explicit review. Deletion checks the opened file's path, reparse and link state and reviewed digest while holding the same exclusive file and ancestor directory handles, then marks that handle for deletion.
- `uv run pytest -q tests/test_agent.py tests/test_edits.py`: 17 passed, including a mocked Strands deletion, rejection and stale-content tests. Ruff lint and `uv run mypy src` passed.
- Non-Windows file mutations remain disabled. Directory creation, independent review, broader filesystem testing and crash-recovery UX are still outstanding.

## 2026-11-02 — Container-free sandbox on Windows through WSL

- After the user clarified that Docker is optional, Docker Desktop was stopped. The implementation uses a WSL Bubblewrap backend instead: a filtered workspace snapshot is extracted into bounded temporary memory, host home and Windows mounts are absent, networking is unshared, the environment is cleared, and the user reviews each command separately. No host shell fallback is present.
- `HARNESS_TEST_WSL=1 uv run pytest -q tests/test_sandbox.py`: 7 passed with real local WSL. Checks include actual passing and failing unittest runs, blocked outbound networking, hidden host paths and environment canary, unchanged live files, timeout, output cap and cancellation with no remaining test process. The mocked Strands agent also invoked a real approved sandbox command successfully.
- Default `uv run pytest -q`: 35 passed, 5 WSL-dependent checks skipped. Ruff and mypy passed during integration. The sandbox shares the WSL kernel, trusts its configured distribution, and has per-process memory limits but no aggregate cgroup memory quota. Binary, oversized and inaccessible files are omitted and reported; dependency directories are excluded.
- Strands Box currently documents Windows and Linux as unsupported. The SDK and Deep Agents both have pluggable sandbox APIs; the current Strands tool uses our audited boundary directly and does not add a second agent loop.

## 2026-11-02 — Workspace-scoped durable sessions

- Added a SQLite storage adapter for the installed Strands snapshot manager, bound session IDs to the canonical workspace, and implemented `/sessions`, `/resume ID`, `/new`, `/clear`, `/status`, and `chat --resume`.
- Only completed turns are saved. An offline model failure immediately after an approved file edit did not replay that edit or restore a pending tool call when a new agent was created. Conversation restoration across a new agent instance and workspace isolation are covered by tests.
- `uv run pytest -q tests/test_sessions.py`: 4 passed. The snapshot callback specialization depends on the locked Strands SDK version. The SQLite database is unencrypted and contains conversation and tool context; in-progress failed turns are intentionally not restored.

## 2026-11-02 — Context window management

- Enabled the installed Strands sliding-window conversation manager with a 40-message window and per-model-call checks. `/compact` explicitly removes older messages, reports how many were removed, and saves the reduced snapshot. It does not claim to summarize them.
- `uv run pytest -q tests/test_sessions.py`: 5 passed, including restoring manually compacted context after constructing a new agent. Ruff and mypy passed after enabling the manager. Token-based summarization is not implemented.

## 2026-11-02 — Optional scoped explorer

- Added an opt-in `explore_repo` Strands agent-as-tool, with only the existing guarded repository read tools and a fresh context for each call. Parent request cancellation and usage limits propagate through the installed SDK's agent-as-tool implementation. The feature is off by default and may incur additional model calls.
- `uv run pytest -q tests/test_streaming.py tests/test_agent.py`: 13 passed, 1 WSL-dependent test skipped. Tests cover actual mocked delegation through the specialist and verify a protected `.env` is not returned. Ruff and mypy passed.

## 2026-11-02 — Explicit project guidance and skills

- Added opt-in `--project-instructions` and repeatable `--skill NAME` for `ask` and `chat`. The guarded workspace reader loads only selected text; no skill scripts execute and selecting guidance does not alter the registered tool permissions.
- On session restore, the current runtime's system prompt and selected guidance replace the stored prompt, so a prior session cannot silently re-enable outdated guidance or policy instructions.
- `uv run pytest -q tests/test_instructions.py tests/test_agent.py tests/test_sessions.py`: 19 passed, 1 WSL-dependent test skipped. Ruff and mypy passed. Skill resources beyond the selected `SKILL.md` are not loaded automatically.

## 2026-11-02 — Interactive model selection

- Added `/model MODEL_ID` to switch the current chat's Bedrock model while retaining the completed session, along with `/model` to display the selection. The next inference checks account access; the switch itself does not invoke the model.
- `uv run pytest -q tests/test_sessions.py`: 7 passed. Ruff and mypy passed. A real Bedrock model switch has not been tested because a live inference call has not been authorized.

## 2026-11-02 — Sandboxed MCP and read-only Git tools

- MCP: opt-in `--mcp-config` runs one local stdio server inside the WSL Bubblewrap sandbox from a filtered snapshot. A Strands `BeforeToolCallEvent` hook requires separate approval for every MCP call; startup needs approval too. Tool count (50), definition size and tool-name shape are bounded. Fixed a test typo from the handoff; one Ruff `PLW1509` suppression moved to the `preexec_fn` line (no threads exist before `Popen`).
- `HARNESS_TEST_WSL=1 uv run pytest -q tests/test_sandbox.py tests/test_mcp_integration.py`: 11 passed. Denied call: server never reached; approved call: server reports `isolated` (no `/home`, `/mnt`, `.env`). Remote MCP is not implemented.
- Git: `git_status` and `git_diff` tools run fixed argv with repo-config program execution disabled and protected paths excluded. `tests/test_git_tools.py` covers protected-file hiding, outside paths, non-repo failure and that malicious `core.fsmonitor`/`diff.external` do not execute.
- Default suite: 51 passed, 8 skipped (WSL opt-in). Ruff and mypy clean. Git commands from this session needed `-c safe.directory=...` because the repository is owned by another Windows account.
