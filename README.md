# Edge Code Harness

An in-progress terminal coding assistant. The current implementation provides a
Strands agent backed by an explicitly configured Amazon Bedrock model, plus
guarded repository tools. On Windows it can propose changes to existing UTF-8
files, create new files, or delete files after interactive approval. Completed
chat turns can be resumed from local SQLite storage. 

## MCP (opt-in, local stdio only)

`harness chat --mcp-config server.json` (also `ask`) with
`{"name": "fixture", "command": ["python3", "/workspace/server.py"]}`. No other fields
(env, credentials, URLs) are accepted. Server startup and every tool call need separate typed
approval; denied calls never reach the server. The server runs in the WSL Bubblewrap sandbox
(copied workspace, no network, no host paths, cleared environment, bounded output/time). Remote
MCP is not implemented. Requires Windows + WSL; other platforms refuse to start the server.

## Git inspection

The agent has read-only `git_status` and `git_diff` tools. They use fixed arguments, disable
repository-configured programs (fsmonitor, external diff, textconv, hooks), exclude protected
paths such as `.env` and keys, and cap output at 64 KiB.

## Install

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --python 3.12 --dev
uv run harness doctor
```

Configure the AWS standard credential chain and set `AWS_REGION` and
`BEDROCK_MODEL_ID` to an authorized region and model or inference profile.
For example, set these as environment variables in your shell. The doctor
reports whether configuration names are present without making AWS calls or
checking model access. Model requests can incur charges.

```powershell
$env:AWS_REGION = "us-east-1"
$env:BEDROCK_MODEL_ID = "your-authorized-model-or-inference-profile"
uv run harness ask "Explain the project structure" --workspace .
uv run harness chat --workspace .
```

The CLI sends prompts and accessible source content to the selected Bedrock
model when used. Do not use it on a repository whose content you are not
authorized to send to the model provider. Workspace protections are under
active development and are not a substitute for OS-level isolation.

On Windows, approved edits use an exclusive file handle and guard ancestor
directories against renaming. A recovery journal containing the original bytes
is created under `~/.edge-harness/recovery` before an edit and removed after
success or successful rollback. The journal is not encrypted; protect the user
profile with appropriate OS permissions and keep any remaining recovery file
private. On other operating systems editing is disabled pending a verified
equivalent boundary. Non-interactive input cannot approve edits.

Interactive chat stores completed conversations in
`~/.edge-harness/sessions.sqlite` by default. Use `/sessions` to list sessions
for the current workspace, `/resume ID` to continue one, and `/new` or `/clear`
to start another. `/compact` removes older messages without generating a
summary; automatic context management also uses a bounded message window.
`harness chat --resume ID` also resumes a session. Set
`HARNESS_STATE_DIR` to change the storage directory. The database contains
conversation and tool context in plaintext and inherits your user profile's OS
access controls. An interrupted or failed turn is not saved as a completed turn;
an edit already approved during that turn may still have been applied, but it
will not be automatically replayed on resume.

Use `/model` to display the current model and `/model MODEL_ID` to switch the
current chat to another Bedrock model or inference profile. The switch preserves
the completed session; model access is checked on the next inference request.

Set `HARNESS_ENABLE_EXPLORER=true` to expose an optional Strands exploration
specialist. It receives only the same guarded read tools as the main agent;
it cannot edit files or run commands. It can make additional model calls. Each
top-level request has a 20-model-turn and 32,768-output-token soft budget shared
with the specialist; an individual model response may overshoot a soft token limit.

Project instructions and skills are not loaded automatically. To select them
for a chat, use `harness chat --project-instructions --skill review`. This reads
the workspace's `AGENTS.md` and `.agents/skills/review/SKILL.md` through the
same guarded reader. Up to five skills may be selected by repeating `--skill`.
Only their text is supplied as guidance; scripts are not executed and the files
cannot grant capabilities or override approval policy. Select the flags again
when resuming a session if you want that guidance in the resumed agent.

## Isolated commands on Windows

The command tool uses a non-root WSL distribution with `/usr/bin/python3` and
Bubblewrap installed; Docker Desktop is not required. It defaults to the
`Ubuntu` distribution; set `SANDBOX_WSL_DISTRIBUTION` to select another.
Every command requires a separate interactive approval. The tool copies allowed
UTF-8 files into temporary memory-backed storage, excludes protected paths, and
does not mount the live workspace. Network access is disabled. The result reports
the exit code, bounded output, stop reason, elapsed time and omitted file count.

The backend applies a 60-second maximum wall timeout, 64 KiB output limits,
bounded temporary filesystems, and per-process resource limits. It does not
provide an aggregate memory quota across all processes. It shares the WSL kernel
and assumes the configured distribution's system tools are trusted. Binary,
oversized and inaccessible files are omitted, dependencies in `.venv` and
`node_modules` are not copied, and dependencies are not installed automatically.
Thus a failed test may reflect the sandbox environment. Commands run only in the
copy; any source change still requires a separately approved edit. If WSL,
Bubblewrap or required namespace features are unavailable, execution fails closed.

## Development

```powershell
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

To run the optional WSL isolation tests on a machine with the prerequisites:

```powershell
$env:HARNESS_TEST_WSL = "1"
uv run pytest -q tests/test_sandbox.py
```

The original requirements and implementation evidence live in
[`harness-docs`](harness-docs/README.md) and
[`harness-docs/harness/build-log.md`](harness-docs/harness/build-log.md).
