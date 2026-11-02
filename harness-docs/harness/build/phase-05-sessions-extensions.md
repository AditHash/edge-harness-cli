# Phase 05 — Strands sessions, LangChain retrieval, MCP, evals

## Prerequisites

Phases 0–4 have passed their functional and security gates, with evidence in `harness/build-log.md`.

## Goal

Enable durable, trustworthy developer sessions and measured optional enhancements without changing the primary Strands agent loop.

## Implement

1. Strands-native persistent sessions, `/resume`, `/new`, `/status`, `/diff`; verify installed persistence API and crash recovery. Never replay previously executed patches/commands.
2. Tool-call idempotency and write journal; crash-safe pending approval state.
3. Lexical retrieval benchmark first. Add LangChain documents/splitters/retrievers/vector store **only if** it improves retrieval accuracy on realistic coding tasks. Search scope and cached hits must reapply protected-path policy.
4. Strands-native MCP and optional skills after explicit user opt-in. All tool actions pass authorization and budget limits; no remote data export by default.
5. Optional deterministic LangGraph workflow exposed as a single bounded Strands tool, only with documented requirement and dedicated tests. Do **not** embed a second autonomous root loop. Deep Agents stays an external benchmark or an intentionally isolated specialist behind an approved snapshot and budget.
6. Cost/usage summaries; opt-in observability with redaction. Benchmark small Bedrock models vs larger models where available; capture actual latency and success rates.

## Acceptance

- Resume after restart preserves conversation without repeating any edit or command.
- LangChain retrieval cannot return secrets/outside-root data, including from stale cached indexes.
- MCP is disabled by default; denied tool calls cannot bypass central policy.
- Same evaluation set runs with lexical baseline and optional LangChain retrieval; results and costs recorded.
- Windows and Linux limitations tested and documented.
