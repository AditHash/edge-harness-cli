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
| Subagents (on by default) | `spawn_subagent`: `explore`, `review`, `test`, `edit` roles; as many as the task needs, one level deep, 8 at once by default, 2M tokens per turn by default |
| Web (on by default) | `web_fetch` reads a page from a public address; each fetch needs approval. There is no web search |
| Real machine commands | `run_on_host` for what the sandbox cannot do, such as `git push`; type `yes` for each command |
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
cd C:/path/to/any/repo; edge-harness         # chat in the current folder
edge-harness --tui | --resume ID | --show-config | --profile NAME | --no-login
```

Region, model and profile are saved in `~/.edge-harness/settings.json` (never credentials;
environment variables win). Before chat starts, credentials are checked with STS
`GetCallerIdentity` (no inference). If they are missing or expired and you are at a terminal,
`aws login` runs automatically; otherwise it tells you to run it and stops.

## Chat display and approvals

Each proposed edit appears as a colored diff panel titled with the file and its +/- line counts;
sandbox commands appear in their own panel. Type `y` to approve one change, or `a` to approve
this and every later file change (or command) for the rest of the chat - diffs are still shown,
`/approve ask` turns it off, and MCP calls always ask. Replies render as Markdown, tool calls show
their target (`> read_code README.md`), failed tools are red, each model call prints a dim usage
line, and every turn ends with a token rule plus a "Changed this turn" table. `/changes` lists all
approved changes in the session.

At a terminal the input box has a live status bar: model, folder, approval mode, context left, and session tokens. Set `NO_COLOR=1` to turn colors off. Close running sessions before `uv tool install --editable . --force`; on Windows they lock the tool folder.

## Sessions, images, skills and MCP

**Resume.** Conversations are saved in `~/.edge-harness/sessions.sqlite`, per folder.
`edge-harness --continue` resumes the latest; `--resume <id>` or `/resume <id>` take a full id or
a unique prefix of at least 4 characters. `/sessions` lists title, age and turns.

**Images.** `/image <path> [message]` attaches a PNG, JPEG, GIF or WebP (up to 3.75 MB, 5 per
message), quoted paths included. You choose the file, so it may be outside the repo, but its
header must be a real image; a renamed secret is refused. `auto` sends images only to models known
to read them. Images are stored in the session and resent with the conversation, which adds cost.

**Skills.** A skill is `.agents/skills/<name>/SKILL.md`, or `<name>/SKILL.md` under
`~/.edge-harness/skills` for every repo; the project one wins. `--skill <name>`, or `/skills` and
`/skill <name>` mid-chat. Skill text is guidance marked untrusted, so nothing in it runs and it
grants no permissions. A model may not always follow it.

**MCP.** `edge-harness --mcp-add server.json` saves a server in your user folder, never the repo;
`--mcp-list`, `--mcp-remove <name>`, `--mcp <name>` to connect, or `/mcp` and `/mcp connect <name>`
mid-chat. Connecting and every tool call need approval. `--mcp-config <file>` connects one without
saving.

## India / Mumbai models and `auto`

```powershell
edge-harness --list-models                     # India (in.*) then Mumbai (ap-south-1) models, by context
edge-harness --probe-models 10                 # check tool support of 10 untested models (small billed calls)
edge-harness --model auto                      # routed: best model per request, with failover
edge-harness --model auto --residency india    # only in.* profiles (requests stay in India)
edge-harness --model auto --residency mumbai   # only models hosted in ap-south-1
edge-harness --model qwen.qwen3-coder-480b-a35b-v1:0   # or pick one yourself
```

`auto` ranks India-routed `in.*` profiles first, then Mumbai on-demand models, largest context
first, among models whose tool-use probe passed. Each model call goes to the best available one;
if it fails before producing output (no access, throttling, outage, context too large) the call
moves to the next model. No access drops a model for the session; throttling cools it down for 2
minutes. Failover never leaves the chosen residency. Context sizes marked `estimate` are
provider-published; Bedrock may serve less.

Auto-compaction summarizes older messages before a model call once input passes 70% of the
model's window (`HARNESS_COMPACT_THRESHOLD`); routed mode uses the smallest window in the route.
`/context` shows usage. The model's thinking is printed dimmed with an estimated token count, and
each model call prints its input/output tokens (`HARNESS_SHOW_THINKING=false` shows a spinner instead).

## Configure and run

```powershell
$env:AWS_REGION = "ap-south-1"
$env:BEDROCK_MODEL_ID = "<model or inference-profile id you have access to>"
uv run harness chat --workspace C:\path\to\repo
uv run harness tui  --workspace C:\path\to\repo
uv run harness ask "Explain the architecture" --workspace C:\path\to\repo
```

Settings (environment variables): `AWS_REGION`, `AWS_PROFILE`, `BEDROCK_MODEL_ID`, `BEDROCK_MAX_TOKENS`,
`SANDBOX_WSL_DISTRIBUTION`, `HARNESS_STATE_DIR` (default `~/.edge-harness`),
`HARNESS_ENABLE_HOST_COMMANDS` (default `true`), `HARNESS_ENABLE_WEB` (default `true`), `HARNESS_ENABLE_SUBAGENTS` (default `true`), `HARNESS_SUBAGENT_MAX_PARALLEL` (default `8`), `HARNESS_SUBAGENT_TURN_TOKENS` (default `2000000`, `0` for no cap), `HARNESS_ENABLE_EXPLORER`, `HARNESS_ENABLE_REVIEWER`, `HARNESS_ENABLE_TESTER` (all `true`/`false`),
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
