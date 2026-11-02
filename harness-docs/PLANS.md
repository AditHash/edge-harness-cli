# PLANS.md — Hybrid harness implementation roadmap

**Rule:** One phase at a time. A checked box means fully implemented and verified; not just planned or scaffolded. Read `docs/HYBRID_INTEGRATION.md` and `docs/SECURITY.md` first.

## Phase 0 — Foundation + SDK validation

- [ ] `uv` Python 3.11+ project, `src/` package, Typer CLI, Ruff, Pytest, typed config.
- [ ] `harness --help`, `harness doctor` without AWS credentials or paid calls.
- [ ] Read `docs/SDK_NOTES.md`; inspect installed Strands Harness/SDK versions and verify Bedrock and custom tool APIs on a disposable fixture.
- [ ] Define a minimal `SearchService` protocol and normalized `AgentEvent` contract (no LangGraph / Deep Agents runtime dependency).
- [ ] Offline deterministic tests and `harness/build-log.md` evidence.

**Gate:** CLI installs; offline tests pass; no model-generated writes or shell commands.

## Phase 1 — Strands + Bedrock read-only agent

- [ ] Configure Strands native Bedrock model with account-selected model/region/profile.
- [ ] Use `create_harness(builtin_tools=[], tools=[...])`; register **only own policy-enforced read-only tools**.
- [ ] Implement `harness ask`, bounded listing/read/search, protected path restrictions, no read outside workspace.
- [ ] Add exactly one **bounded LangChain component integration test** (e.g. `Runnable` search service inside a Strands tool); allow implementation to defer indexing if it adds no value yet.
- [ ] Offline fake-model tests and opt-in Bedrock smoke test.

**Gate:** explanation cites fixture files; secret/outside paths denied; write/shell tools absent; component adapter validated.

## Phase 2 — Interactive streaming CLI

- [ ] `harness chat`, Rich streaming, tool start/result/error output, status and cancellation.
- [ ] `/help`, `/new`, `/status`, `/exit`, optional usage data; test multistep read-only turns.
- [ ] Normalize Strands events; no UI dependence on raw SDK message format.

**Gate:** visible accurate streamed turns, no accidental side effects on cancel.

## Phase 3 — Staged code edits + approvals

- [ ] Introduce patch proposal tools and precise unified diff before any write.
- [ ] Human decision via Strands intervention/UI *and* independent policy enforcement on apply.
- [ ] Protect secrets/out-of-root paths, prevent traversal/symlink/junction escapes, reject stale diffs.
- [ ] Denial, timeout, changed-since-review, no-op and conflict tests.

**Gate:** approved patches apply exactly; all other cases leave bytes unchanged.

## Phase 4 — Isolated command execution

- [ ] Implement Docker or verified sandbox contract with a temporary **copied** workspace, no writable host mount and no credentials.
- [ ] Deny network by default; set CPU/memory/process/runtime/output limits and cancellation.
- [ ] Separate command approval from write approval; show actual exit code and test evidence.
- [ ] Negative-path security tests for outside-root reads and network access.

**Gate:** commands run only in isolation, test failures report truthfully, host state unchanged.

## Phase 5 — Sessions, LangChain retrieval, MCP and evaluations

- [ ] Strands-native durable sessions and crash-safe approval recovery without replaying writes.
- [ ] Measure lexical code search vs optional LangChain chunked/vector retrieval; add semantic indexing only if benchmark benefit justifies it.
- [ ] Opt-in Strands MCP / skills with per-tool authorization.
- [ ] Optional deterministic LangGraph workflow or isolated Deep Agents specialist only after explicit benchmark-based decision.
- [ ] Reproducible test suite, cost/latency metrics, sanitized tracing (LangSmith or OTel integration as warranted).

**Gate:** resume is safe, retrieval cannot leak protected content, extensions do not bypass permissions.

## Release checks

- [ ] Tests and lint run with results recorded in `harness/build-log.md`.
- [ ] Windows/Linux behavior and Docker prerequisites documented.
- [ ] All invariants in `docs/SECURITY.md` verified.
- [ ] No default cloud mutations, autonomous commits, credential reads, or host execution.

## Implementation status (2026-11-02, evidence in `harness/build-log.md`)

Implemented and tested offline: Phases 0-5 including sandboxed local and remote MCP with per-call
approval, read-only Git tools, summarizing/proactive compaction, planning, opt-in explorer, reviewer
and test-runner specialists, cumulative usage, Textual UI, ranked LangChain retrieval (benchmarked),
POSIX reviewed edits, native Linux sandbox backend, repository-analysis and debugging scenario tests.

Live Bedrock (Scenario G) verified for streaming, resume, tool-using analysis and compaction (build log).
Live edits, sandboxed debugging, local MCP and the TUI also verified with a real model (script approvals).
Not done / unverified: remote MCP with a real model; macOS command sandbox (disabled by design); native Linux and POSIX edits verified
only through WSL's Linux Python, not a separate Linux machine; semantic embeddings; sanitized
tracing; LangGraph and Deep Agents (rejected for lack of measured benefit).
