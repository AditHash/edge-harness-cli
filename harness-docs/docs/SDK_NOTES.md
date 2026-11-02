# SDK_NOTES.md — Verify APIs in the installed environment

**As of 2026-11-02: documentation research only. No SDK has been installed or executed as part of this pack.** Do not copy examples blindly. `uv.lock` must reflect versions tested by Codex in the actual implementation repo.

## Core dependencies

- `strands-harness` — `from strands_harness import create_harness` factory for root runtime.
- `strands-agents` — the underlying Strands agent SDK; tool decorator `from strands import tool`.
- Strands-native Bedrock integration — use explicit provider/model configuration, profile and region supported by installed version. Verify model string or BedrockModel constructor and tool/stream support.
- `typer`, `rich`, `pydantic-settings`, `pytest`, `ruff`, `boto3` only as needed.
- `langchain-core` — optional `Document` / `RunnableLambda` service implementation behind a Strands `@tool`.
- Add `langchain-text-splitters`, retriever/vector packages only if retrieval benchmark warrants them. Do not add `langgraph`, `deepagents`, `langchain-aws`, or `langchain-mcp-adapters` to the MVP by default.

## API verification checklist

- [ ] Record Python/uv/Strands Harness/Strands SDK versions and supported model interface.
- [ ] Verify `create_harness(model=..., builtin_tools=[], tools=[...])` and introspect registered tools.
- [ ] Verify how to construct Bedrock model with region/profile/inference profile and whether async/stream tool-calling is supported on chosen model.
- [ ] Verify `@tool` schema conversion and tool output/error mapping.
- [ ] Verify disabling built-ins also disables web/programmatic subagent tools, and no plugin silently exposes unsafe tools.
- [ ] Verify event-stream, cancellation, interception and sessions API for pinned versions.
- [ ] Verify Strands custom tools run with process privileges; tool-local root/path checks necessary.
- [ ] Verify LangChain Runnable import and deterministic adapter test without LLM calls.
- [ ] Verify optional sessions/MCP/plugins/sandbox APIs when implementing phases 4–5.
- [ ] Verify Windows/PowerShell support for environment config and sandbox launch.

## Operational notes

- AWS SDK standard credential resolution only; never require secrets in project files.
- Model/inference profile availability and cost depend on AWS account and region; explicit live smoke tests may incur costs.
- Keep model/provider errors distinguishable from authorization, unsupported tools and throttling.
- Do not enable unrestricted default shell/file tools merely because docs show them in a quickstart.

## Official references

- https://strandsagents.com/docs/user-guide/harness/
- https://strandsagents.com/docs/user-guide/harness/composing-with-sdk/
- https://strandsagents.com/docs/user-guide/harness/configure/tools-and-instructions/
- https://strandsagents.com/docs/user-guide/harness/reference/configuration/
- https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools/
- https://strandsagents.com/docs/user-guide/harness/configure/interventions/
- https://docs.langchain.com/oss/python/deepagents/retrieval
- https://docs.langchain.com/oss/python/langchain/mcp
