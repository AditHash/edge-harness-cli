# Edge Code Harness

A security-conscious terminal coding agent. One Strands agent (via Strands Harness) runs the
conversation against Amazon Bedrock. Every side effect passes through a policy-checked tool and a
typed human approval. LangChain supplies a ranked-retrieval component. No second agent loop exists.

Status: feature-complete against the project plan except the items under **Known limitations**.
Live Bedrock streaming, session resume, tool use and `/compact` were verified once on 2026-11-02
(ap-south-1; see the build log). Use a capable tool-using model: a small model ignored the repo tools.
Setup and a bounded smoke test: [Bedrock guide](harness-docs/docs/BEDROCK.md).

## Capabilities

| Area | What it does |
| --- | --- |
| Chat | `harness chat` (Rich, streaming) and `harness tui` (Textual, modal approvals) |
| Models | Bedrock via Strands; `/model ID` switches mid-session |
| Repo tools | `list_repo`, `read_code`, `search_code`, `find_relevant_code` (BM25, LangChain `BaseRetriever`), `git_status`, `git_diff` |
| Edits | create / edit / delete with exact-diff review; Windows (exclusive handles) and Linux/macOS (pinned directory fds) |
| Commands | `run_sandboxed` in a copied workspace, no network; Windows→WSL Bubblewrap, Linux→native Bubblewrap; macOS disabled |
| Sessions | SQLite, workspace-scoped; `/sessions`, `/resume ID`, `/new`, `/clear` |
| Context | Strands summarizing manager; proactive compaction at 70% of context; `/compact` |
| MCP | local stdio servers inside the sandbox, or remote HTTPS servers; every call separately approved |
| Planning | `update_plan` tool, `/plan` view, persisted in session state |
| Specialists (opt-in) | `explore_repo`, `review_changes` (read-only), `run_tests` (sandbox, approved) |
| Usage | per-turn tokens, `/usage` session totals, optional cost from user-set prices |
| Guidance | `--project-instructions`, `--skill NAME`; explicit, never executed |

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). `aws login` credentials work (the
`botocore[crt]` dependency is included).

```powershell
uv sync --python 3.12 --dev
uv run harness doctor
```

Platform prerequisites:

- **Windows**: WSL with an Ubuntu distribution, a non-root user, and `bubblewrap` installed
  (`sudo apt install bubblewrap`). Set `SANDBOX_WSL_DISTRIBUTION` if the distro is not `Ubuntu`.
- **Linux**: `/usr/bin/bwrap` with unprivileged user namespaces, `/usr/bin/python3`, a non-root user.
- **macOS**: chat, read tools, Git, edits and remote MCP work. Command execution and local MCP stay
  **disabled** (no verified containment); `harness doctor` says so.

## Quick start: `edge-harness`

```powershell
uv tool install --editable .            # puts edge-harness on PATH (~/.local/bin)
edge-harness --region ap-south-1 --model <model-id>   # first run: saved for next time
cd C:\path	onyepo; edge-harness              # chat in the current folder
edge-harness --tui | --resume ID | --show-config | --profile NAME | --no-login
```

Region, model and profile are saved in `~/.edge-harness/settings.json` (never credentials;
environment variables win). Before chat starts, credentials are checked with STS
`GetCallerIdentity` (no inference). If they are missing or expired and you are at a terminal,
`aws login` runs automatically; otherwise it tells you to run it and stops.

## Configure and run

```powershell
$env:AWS_REGION = "ap-south-1"
$env:BEDROCK_MODEL_ID = "<model or inference-profile id you have access to>"
uv run harness chat --workspace C:\path\to\repo
uv run harness tui  --workspace C:\path\to\repo
uv run harness ask "Explain the architecture" --workspace C:\path\to\repo
```

Settings (environment variables): `AWS_REGION`, `AWS_PROFILE`, `BEDROCK_MODEL_ID`,
`SANDBOX_WSL_DISTRIBUTION`, `HARNESS_STATE_DIR` (default `~/.edge-harness`),
`HARNESS_ENABLE_EXPLORER`, `HARNESS_ENABLE_REVIEWER`, `HARNESS_ENABLE_TESTER` (all `true`/`false`),
`INPUT_PRICE_PER_MTOK`, `OUTPUT_PRICE_PER_MTOK`. Full guides: [CLI reference](harness-docs/docs/CLI.md),
[Bedrock setup](harness-docs/docs/BEDROCK.md), [Security](harness-docs/docs/SECURITY.md),
[Architecture](harness-docs/ARCHITECTURE.md).

## MCP

`--mcp-config server.json`, on `ask`, `chat`:

```json
{"name": "fixture", "command": ["python3", "/workspace/server.py"]}
```

runs a local stdio server in the sandbox (copied workspace, no network). Or a remote server:

```json
{"name": "docs", "url": "https://example.com/mcp"}
```

Remote rules: HTTPS only, no credentials/headers/query strings, destination must resolve to
public addresses, redirects stay on-origin. Both kinds show startup details and require typing
`yes`; every tool call shows its exact arguments and needs its own `yes`. Denied calls never reach
the server. MCP output is untrusted model input.

## Develop

```powershell
uv run ruff format src tests; uv run ruff check src tests; uv run mypy src
uv run pytest -q                                   # default suite
$env:HARNESS_TEST_WSL='1'; uv run pytest -q        # adds real sandbox/MCP/scenario tests
```

Tests marked skipped are **not** passed tests; run the opt-in suites on a machine that supports
them. POSIX edit tests run only on Linux/macOS (see build log for how they were run in WSL).

## Known limitations

- Live runs used script-granted approvals (`scripts/live_scenarios.py`); remote MCP was not run live. Summaries are lossy.
- macOS command sandbox is not implemented; native Linux was exercised only through WSL's Linux.
- The sandbox shares the host kernel (no aggregate cgroup quota); trust the distribution.
- Retrieval is lexical BM25. Semantic embeddings would send repository text to a provider.
- SQLite sessions are unencrypted and contain conversation and tool output.
- LangGraph and Deep Agents are intentionally absent (no measured benefit over Strands).
- Remote MCP DNS-rebinding between validation and connect is not fully closed.

## Troubleshooting

- `Git unavailable`: install Git, or the repo is owned by another account (Git `safe.directory`).
- `No sandbox available`: see platform prerequisites above; `harness doctor` shows the backend.
- `Cannot start agent`: set `AWS_REGION` and `BEDROCK_MODEL_ID`; check `harness doctor`.
- `Agent failed (...)`: provider details are hidden by design; check model access in your account.
