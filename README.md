# Edge Code Harness

An in-progress terminal coding assistant. The current implementation provides a
Strands agent backed by an explicitly configured Amazon Bedrock model, plus
guarded repository tools. On Windows it can propose changes to existing UTF-8
files, create new files, or delete files after interactive approval. It is not
yet a complete coding agent: durable sessions and MCP are not enabled.

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
