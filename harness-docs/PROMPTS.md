# PROMPTS.md — Instructions to give Codex

`AGENTS.md` is the repository's standing rule set. Give Codex only one phase at a time. These prompts are **instructions**, not an executed implementation.

## Phase 0 — Start here

```text
Read AGENTS.md, GOALS.md, ARCHITECTURE.md, docs/HYBRID_INTEGRATION.md,
docs/SECURITY.md, docs/SDK_NOTES.md, PLANS.md and
harness/build/phase-00-foundation.md.

Implement PHASE 0 ONLY. Inspect and preserve existing files. Set up a uv-managed
Python 3.11+ project, Typer CLI, validated configuration, read-only `doctor`
command, and offline pytest/ruff. Verify installed Strands Harness and Strands
SDK signatures in a disposable environment; add no billed LLM calls. Define
interfaces for normalized events and a scoped search service. Record exact
commands/results in harness/build-log.md, check only verified boxes, STOP.
```

## Phase 1 — Strands + Bedrock + one LangChain tool adapter

```text
Read the project rules and phase-01-bedrock-agent.md. Implement a single Strands
Harness primary agent loop with Bedrock and only custom, workspace-policy-checked
read-only tools. Use create_harness(builtin_tools=[], tools=[...]); verify the
installed API, do not expose built-in host shell/read/write/edit. Add one
minimal LangChain Runnable or retrieval service behind a Strands @tool adapter,
without creating a second agent loop. Use offline mocked tests and a separately
opt-in Bedrock smoke test. Protect secrets and paths with tested code. Do not
implement writes or command execution. Update PLANS.md/build-log and STOP.
```

## Phase 2 — Streaming CLI

```text
Follow AGENTS.md and harness/build/phase-02-interactive-cli.md. Add Rich
streaming to the existing Strands agent through an AgentEvent abstraction.
Implement /help /new /status /exit and cancellation. Read-only tools only.
Add fake-event tests, report verified results and STOP.
```

## Phase 3 — Safe edits

```text
Follow docs/SECURITY.md and harness/build/phase-03-approved-edits.md.
Implement staged patch proposals, exact unified diff review, Strands human
interventions and a separate fail-closed patch-application policy. Test
denial, stale content, protected paths, symlink/junction escapes and rollback.
No shell. Report evidence and STOP.
```

## Phase 4 — Sandbox

```text
Follow docs/SECURITY.md and harness/build/phase-04-sandbox-execution.md.
Implement OS-isolated test/build command execution via a copied workspace,
no host-generated shell execution, no network/host secrets by default, resource
limits and cancellation. Commands and workspace writes require independent
approval. Negative-path tests required. Report evidence and STOP.
```

## Phase 5 — Sessions and optional extras

```text
Follow phase-05-sessions-extensions.md. Add Strands durable sessions and
idempotent resume. Benchmark lexical repository search against optional LangChain
retrieval and add only measured improvements. Use Strands-native MCP with opt-in
permissions. Do not add LangGraph/Deep Agents unless a scoped integration case
wins tests. Record model costs, redaction and evaluation evidence. STOP.
```

## Independent review prompt

```text
Review current changes for correctness and security without editing first.
Prioritize external-path reads, secrets, symlink/junction escapes, cached-index
leaks, unsafe MCP tools, prompt injection, interrupt replay, unapproved writes,
host command execution, Windows/Linux differences and missing tests. Report
findings with file/line locations, severity and commands actually run.
```
