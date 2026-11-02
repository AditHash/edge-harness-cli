# Phase 02 — Interactive CLI and streaming

## Prerequisite

Read-only agent from Phase 01 works and its policy tests pass.

## Goal

An ergonomic multi-turn terminal experience with truthful tool visibility, graceful failure and cancellation.

## Implement

- `harness chat [--workspace PATH]`, a REPL using Rich + Typer. No full Textual application unless justified.
- Show model output incrementally when supported. Show readable tool start/end status, bounded redacted arguments and outputs, and running/final state.
- Internal `AgentEvent` abstraction decoupled from specific Strands streaming API.
- Commands `/help`, `/new`, `/status`, `/exit`; user can Ctrl+C to cancel an active invocation without crashing the CLI.
- Rate-limit/throttle, auth/model-unavailable and network error messages.
- Track session thread ID in memory for this phase. Keep filesystem read-only.

## Acceptance

- Two prompts in one session preserve relevant context.
- Fake streams produce deterministic UI tests; no duplicate/truncated success markers.
- Cancellation and exceptions yield clear terminal state.
- No write/shell operation becomes available accidentally.

## Out of scope

Durable resume, code edits, Docker commands, MCP and subagents.
