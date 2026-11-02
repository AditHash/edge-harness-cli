# Edge Code Harness

An in-progress terminal coding assistant. The current implementation provides a
read-only Strands agent backed by an explicitly configured Amazon Bedrock model,
plus guarded repository tools. It is not yet a complete coding agent: edits,
sandbox execution, persistent sessions and MCP are not enabled.

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

## Development

```powershell
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
```

The original requirements and implementation evidence live in
[`harness-docs`](harness-docs/README.md) and
[`harness-docs/harness/build-log.md`](harness-docs/harness/build-log.md).
