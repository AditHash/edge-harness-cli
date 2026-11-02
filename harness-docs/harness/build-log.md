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
