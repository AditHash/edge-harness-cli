# SDK_NOTES.md — Verify APIs in the installed environment

## Observations during implementation (2026-11-02)

- Python 3.12.10, Strands Harness 0.3.3, Strands Agents 1.63.0; exact dependencies are in `uv.lock`.
- `create_harness` accepts a `Model`, `builtin_tools=[]`, and separate `skills`, `memory`, `session`, `builtin_plugins`, and `background_tasks` switches. An offline construction test confirms exactly three custom tools registered when these are disabled.
- Strands `BedrockModel` accepts `model_id`, `region_name`, and `max_tokens`; `Agent.stream_async` exposes text chunks as `data` events. No live model invocation has been authorized or executed.
- Deep Agents Code was inspected at commit `311ed7fb0e496b93ca56fb0b8e46ed0e5355600f`: its separation of presentation from runtime, command catalog, threat model, session recovery tests, and trust treatment of project extensions are useful references. Its LangGraph server is not inserted into the Strands loop.
- The Strands SDK exposes custom sandbox boundaries and a cancellation signal in `ToolContext`; the CLI's WSL Bubblewrap adapter uses a custom Strands tool with that signal. Deep Agents also offers pluggable sandbox backends, but no second agent loop is needed for command execution. Strands Box's published support table currently lists Windows and Linux as unsupported, so it is not used as the local backend. The Harness subagent documentation says the built-in generalist inherits its parent's configuration; it remains disabled until our tool and approval inheritance are verified.
- Strands 1.63.0 documents `SnapshotSessionManager` as the snapshot-based path that can retain context-manager state. The implementation supplies a SQLite `Storage` adapter and uses one Strands session manager. Its pinned-version callback is specialized to save only successful completed turns, avoiding automatic replay of an interrupted side effect; the tradeoff is that in-progress conversation is not restored after failure.

The original documentation pack contained research only. See the implementation observations above and `uv.lock` for the installed versions; do not copy unverified examples blindly.

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
