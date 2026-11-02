# Phase 00 — Repository foundation

## Goal

Build an installable, testable Python CLI skeleton with configuration and read-only environment diagnostics. **No AI calls, agent filesystem access, or shell execution.**

## Implement

- `pyproject.toml` with Python >=3.11, `src/code_harness/` package, console entry `harness`, and uv dependency lock.
- Typer commands: `harness --help`, `harness doctor`.
- Pydantic Settings configuration for `AWS_PROFILE` (optional), `AWS_REGION`, `BEDROCK_MODEL_ID`, workspace root, and safe verbosity flags. Bedrock model ID is required for invocation but **not** for `--help`/offline tests.
- `doctor` checks Python version, workspace validity, AWS configuration presence and optionally identity using a safe AWS API; it must not require AWS credentials just to start. Do not invoke/pay for a model in `doctor` by default.
- `pytest`, `ruff`, small tests for parser, config validation, missing credentials, invalid workspace, and redacted error messages.
- `.gitignore`, `.env.example` containing only names/placeholders, README updated with actual working commands.

## Acceptance

- `uv sync --dev`, `uv run harness --help`, `uv run harness doctor`, `uv run pytest -q`, `uv run ruff check .` run in a configured environment.
- No network needed for offline tests.
- No secret values in stdout/stderr.
- No code path allowing model-initiated write/shell.

## Exclusions

No Strands Harness/Bedrock invocation yet, no `execute` tool, no TUI, no editing, no MCP, no persistence.
