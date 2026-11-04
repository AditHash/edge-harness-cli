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

## 2026-11-02 — Summarizing, token-aware compaction

- Replaced the sliding window with Strands `SummarizingConversationManager` (30% oldest messages summarized, 10 recent kept, proactive compression at the SDK default 70% of the model context window). `/compact` now produces a model-written summary, which costs one model call and sends the old messages to the provider. Summaries may carry untrusted tool output forward.
- Test doubles needed `estimate_utilization`; the compaction test now uses 9 turns. Default suite: 51 passed, 9 skipped; Ruff and mypy clean. Real-model summarization is untested (no authorized Bedrock call).

## 2026-11-02 — Summarizing, token-aware compaction

- Replaced the sliding window with Strands `SummarizingConversationManager` (30% oldest messages summarized, 10 recent kept, proactive compression at the SDK default 70% of the model context window). `/compact` now produces a model-written summary, which costs one model call and sends the old messages to the provider. Summaries may carry untrusted tool output forward.
- Test doubles needed `estimate_utilization`; the compaction test now uses 9 turns. Default suite: 51 passed, 9 skipped; Ruff and mypy clean. Real-model summarization is untested (no authorized Bedrock call).

## 2026-11-02 — Completion pass: platforms, remote MCP, TUI, retrieval, specialists, docs

- Added: cumulative `/usage` (+ optional cost from user prices), `update_plan`/`/plan`, opt-in `review_changes` and `run_tests` specialists, POSIX reviewed edits (`posix_edits.py`), `platforms.py` backend selection (WSL, native Linux, none; macOS fails closed), remote streamable-HTTP MCP (`mcp_remote.py`), Textual `harness tui` with modal fail-closed approvals, `WorkspaceRetriever` (LangChain `BaseRetriever`, BM25) exposed as `find_relevant_code`, `doctor` backend report.
- Retrieval benchmark (`tests/test_retrieval.py`, this repo's own `src`): hit@3 ranked 10/10 vs existing phrase search 0/10. Queries were written by the implementer, so this is an upper bound.
- Verification (all executed this session): `ruff format --check`, `ruff check`, `mypy src` clean (24 files). Windows default `uv run pytest -q -rs`: 76 passed, 14 skipped. Windows with `HARNESS_TEST_WSL=1`: 85 passed, 5 skipped (the 5 are POSIX-only edit tests). Linux Python in WSL (pure-Python pytest from the Windows venv via PYTHONPATH): `test_posix_edits`, `test_sandbox` (native Linux backend, real Bubblewrap, cancel test deselected because it shells to wsl.exe), `test_workspace`, `test_proposals`, `test_approvals`, `test_edits`: 21 passed, 9 skipped (Windows-only edit tests).
- Real HTTP evidence: `tests/test_mcp_remote.py` runs a real streamable-HTTP MCP server on loopback; a denied call never reaches it, an approved call returns `remote-ok:hi`. Public-URL policy tested with injected resolvers only; no external host was contacted.
- Scenario status: A plumbing (realistic fixture repo, secrets hidden) tested; explanation quality needs a live model. B, C, D, E, F tested with scripted models. G not run (no authorization; no AWS inference call was made; the user's `aws login` was the only AWS action).
- Caveats: the TUI test drives a scripted model and a patched `execute_in_sandbox`; macOS and a real separate Linux host are untested; `SummarizingConversationManager` was only exercised with a scripted model.

## 2026-11-02 — Scenario G: live Bedrock (authorized by the user: "any region, any model")

- The authorized AWS account via `aws login` (default profile), region `ap-south-1`. Listing calls (`list-inference-profiles`, `list-foundation-models`) were read-only. Inference used `apac.amazon.nova-lite-v1:0` (cheap smoke) and `in.anthropic.claude-haiku-4-5-20251001-v1:0` (tool-using analysis). About 15 small model calls in total.
- Found and fixed two real defects only a live run exposed: (1) `aws login` credentials need `botocore[crt]`, now a dependency (`MissingDependencyException` otherwise); (2) the Windows cp1252 console crashed on `≥` in model text, so stdout/stderr are now reconfigured to UTF-8 with `errors="replace"`.
- Results: streaming `ask` OK (1422 in / 23 out); two-turn `chat` with `/usage` OK (session total 2885 in / 82 out); `--resume` restored context across a new process (model recalled the first request); Haiku 4.5 `ask` on the shop fixture used `list_repo`, `read_code` and produced an accurate cited architecture summary including `shop/cart.py` bulk discount. Nova Lite did not use `find_relevant_code` or read files and gave a poor answer: model choice matters.
- Real `/compact` after 8 turns: "Compacted 3 older messages into a model-written summary"; the small model then could not recall a detail from a compacted turn, so summaries are lossy.
- Not exercised live: approved edits and sandbox commands (piped stdin is not a TTY, so approvals correctly deny), MCP with a live model, `harness tui` with a live model. Cost not measured; prices unset.

## 2026-11-02 — Live approval-gated scenarios (B, C, F, TUI)

- `scripts/live_scenarios.py` with `in.anthropic.claude-haiku-4-5-20251001-v1:0`, ap-south-1. The script grants approvals in place of a person and prints every approved diff, command and MCP call. Workspaces are under `./.live-smoke` (gitignored).
- B: model read `shop/cart.py`, proposed `BULK_THRESHOLD = 10` to `5` (exact one-line diff), approved, file changed. 5919 in / 427 out.
- C: model ran `python3 -m unittest -q` in the WSL sandbox (fail), read files, fixed `len(values) + 1` to `len(values)` in `calc.py` (not the test), reran (OK) and reported the real result. Two approved commands, one approved edit. 13784 in / 592 out.
- F: model called the sandboxed fixture MCP tool `fixture_probe` once after approval and reported `isolated`. 3150 in / 62 out.
- TUI: headless Textual pilot, one real streamed turn, usage tracked (1462 in / 4 out).
- RESULTS: B, C, F, TUI all True. Human-typed approval at a real TTY was not exercised; the approval functions themselves are covered by offline tests.

## 2026-11-03 — `edge-harness` launcher

- New entry point `edge-harness` (`code_harness/launcher.py`): chat (or `--tui`) in the current folder; `--region/--model/--profile` validated and saved atomically to `<HARNESS_STATE_DIR>/settings.json` (0600, no credentials, tampered values ignored, env wins); STS `GetCallerIdentity` pre-check; interactive `aws login [--profile] [--region]` when credentials are missing/expired, then re-check; non-interactive stops with a message.
- `tests/test_launcher.py`: 12 passed. Full default suite 89 passed, 14 skipped. Ruff and mypy clean.
- Installed with `uv tool install --editable .` (user tools dir). Live: saved ap-south-1 + Haiku 4.5, ran `edge-harness` from `.live-smoke/launcher` with no env vars; chat replied `ok` (1773 in / 4 out). With a nonexistent profile and piped stdin it refused with the login message. The automatic interactive `aws login` path was not exercised live (credentials were valid); it is unit-tested with injected check/login functions.

## 2026-11-03 — Fix: large-file edits hit the output cap

- User report: asking to update a README in a real repo ended with `Turn failed (MaxTokensReachedException)`; `edit_code` needs the whole file in one tool call and `max_tokens` was fixed at 4096.
- Fixes: `replace_in_file(path, old_text, new_text)` (exact unique snippet, same diff review and apply path; CRLF files accept `
` snippets); `BEDROCK_MAX_TOKENS` setting, default 16384; instructions prefer `replace_in_file`; actionable output-limit and expired-credential messages; Strands SDK warnings (which echoed raw tool input) silenced in the CLI.
- Tests: 91 passed, 14 skipped; Ruff and mypy clean. Live (Haiku 4.5, ap-south-1, script approval): an 11 KB README intro update used `read_code` then `replace_in_file`, one 10-line diff, 361 output tokens, file updated correctly.

## 2026-11-03 — India/Mumbai model catalog, routed `auto`, auto-compaction, visible thinking

- `catalog.py`: read-only discovery of `in.*` profiles (India tier) and ap-south-1 on-demand text models (Mumbai tier); embeddings/guard/audio/legacy excluded; context from the Strands SDK table ("sdk") or provider-published values ("estimate"); tool-use probe statuses ok / failed / no_access cached in `<state>/model-catalog.json` (sanitized on load).
- `routing.py`: `RoutedModel` (a Strands `Model`) for `--model auto`: per-call routing over up to 4 probed candidates, tier first then context; failover before first output on no access (dropped), throttling/outage (2-minute cooldown) or context overflow (only larger windows); errors after output are raised, never retried on another model. Compaction uses the smallest live window.
- Auto-compaction: `SummarizingConversationManager` proactive threshold `HARNESS_COMPACT_THRESHOLD` (0.7), with the correct window passed for SDK-unknown models; `compacted` event and `/context` command.
- Thinking: reasoning deltas printed dimmed with a ~token count, plus per-model-call usage; TUI logs thinking blocks and shows counts in its status bar.
- Tests: 114 passed, 14 skipped; Ruff and mypy clean.
- Live (ap-south-1): 14 probes. ok: Haiku 4.5 (in.), Kimi K3 (in.), Kimi K2 thinking, Kimi K2.5, Qwen3 235B/coder 480B/coder 30B/next 80B/VL 235B, Devstral 2. `no_access` (AccessDeniedException, account not entitled): in.anthropic.claude-sonnet-5, claude-opus-5, openai gpt-5.6-luna/terra. Kimi K2 thinking chat showed its reasoning (~44 tokens), per-call usage and `/context` 1,235/262,144. `edge-harness` with `BEDROCK_MODEL_ID=auto` routed to Haiku 4.5 (India tier first). Live failover: a route starting with Sonnet 5 dropped it (no access) and answered with Haiku 4.5 in the same call.

## 2026-11-03 - Session approvals and chat display

- User feedback: typing `yes` for every change was tedious, and the output made it hard to see what changed.
- Approvals: `all` grants a per-chat standing approval for edits or for commands (separately); `/approve`, `/approve ask`. MCP always asks; non-interactive never approves.
- Display (Rich, no new dependency): diff and command panels with +/- counts, Markdown replies via `Live`, `> tool target` lines, red failures, dim per-call usage with the serving model, a turn rule with totals, a "Changed this turn" table and `/changes`, header panel, styled prompt. Panels avoid non-cp1252 glyphs after a crash on a legacy console.
- Tests: 128 passed, 14 skipped; Ruff and mypy clean. Live (Haiku 4.5 via auto, India): header, Markdown reply, tool lines, per-call usage and turn rule rendered; found and fixed reply/usage sharing a line when output is piped. The interactive approval prompt was not exercised live (needs a TTY); its rendering and recording are covered by tests.
- Security: the AWS account id was redacted from build-log history before the first push (local `filter-branch`; nothing had been pushed). Push to github.com/AditHash/edge-harness-cli was refused: `gh` is logged in as `adeywm`, which has no push permission (403).

## 2026-11-04 - Sessions, images, skills, MCP registry, git_log

- edge-harness exposes `--skill`, `--project-instructions`, `--mcp-config`; `--tui --resume` now works (it was silently ignored).
- Sessions: metadata (title, last used, turns), `/sessions` table, unique-prefix ids, `--continue`. Older sessions stay listed without a title.
- MCP: user-level `mcp-servers.json`, `--mcp-add/--mcp-remove/--mcp-list`, `--mcp NAME` (repeatable), `/mcp`, `/mcp connect NAME`.
- Skills: global folder, discovery, `/skills`, `/skill NAME`.
- Images: `/image` with header validation; `RoutedModel` sends image conversations only to Claude and Kimi K3.
- `git_log` tool; sandbox copy keeps real modification times.
- Tests: default 199 passed, 14 skipped; `HARNESS_TEST_WSL=1`: 208 passed, 5 skipped (POSIX-only). Ruff and mypy clean.
- Live (ap-south-1): a real red PNG answered `Red` on Kimi K3 and Haiku 4.5, and both recalled it on a second turn; auto with text-only Qwen first skipped it and answered via Kimi K3. `--continue` recalled a code word from the previous process. A skill loaded by flag and by `/skill` reached the system prompt; the model followed it in 2 of 3 runs each way. A saved MCP server with piped stdin was refused at the approval step. `git_log` listed real commits on a repo owned by this user; on a repo owned by another account Git refuses (`safe.directory`). The sandbox reported the real mtime.
- Not exercised live: `/mcp connect` and `--mcp` with human approval at a terminal, `--tui --resume`, GIF/WebP/JPEG uploads, and large images.

## 2026-11-04 - Subagents

- Added `spawn_subagent` (on by default) with `explore`, `review`, `test` and `edit` roles. Subagent tokens roll into the turn's usage and `/usage`; progress lines show each subagent's tools.
- Tests: default 213 passed, 14 skipped; `HARNESS_TEST_WSL=1`: 222 passed, 5 skipped (POSIX-only). Ruff and mypy clean.
- Live (ap-south-1): Kimi K3 and Haiku 4.5 each ran two `explore` subagents in one response and combined the reports. An `edit` subagent with piped stdin was refused at approval and the file stayed unchanged. Asked for a broad audit of a two-file repo, Kimi K3 did the work itself and spawned nothing.
- Not exercised live: approving a subagent's edit at a terminal, `test` role commands, Ctrl-C during a running subagent (the stop signal is covered by unit tests only), `--tui`.

## 2026-11-04 - Dynamic subagent count

- Removed the fixed per-turn count. `HARNESS_SUBAGENT_MAX_PARALLEL` (default 8) queues extras, and `HARNESS_SUBAGENT_TURN_TOKENS` (default 2,000,000 uncached, `0` for no cap) stops new and queued subagents.
- Tests: default 216 passed, 14 skipped; `HARNESS_TEST_WSL=1`: 225 passed, 5 skipped (POSIX-only). Ruff and mypy clean.
- Live (ap-south-1, before the queued-budget check): Haiku 4.5 spawned 12 `explore` subagents in one response; 12 finished, 0 failed, correct combined answer, 8s, 34,497 in / 2,360 out. The budget stop has not been hit on a real model.
- One `HARNESS_TEST_WSL=1` run right after the default suite had 3 failures (names not captured); the next two runs on the same code had none. Cause unknown, so a WSL test may be flaky.

## 2026-11-04 - Web search and fetch

- Added guarded `web_search` (Exa) and `web_fetch` for the main agent and `explore` subagents, with separate approvals.
- Not exercised live: approval prompts at a terminal, and a real model choosing these tools (the AWS login had expired during this work).
- Tests: default 246 passed, 14 skipped in 47.59s; `HARNESS_TEST_WSL=1`: 255 passed, 5 skipped in 64.65s (0:01:04). Ruff and mypy clean.
- Fix: added certifi roots after Python on Windows refused example.com while `system+certifi` accepted it; expired, self-signed and wrong-host certificates stayed refused. HTML comments no longer reach page text. Tests: default 247 passed, 14 skipped in 37.87s; `HARNESS_TEST_WSL=1`: 256 passed, 5 skipped in 65.56s (0:01:05).
