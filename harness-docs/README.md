# Hybrid Code Harness — Codex development documentation

A documentation-first plan for an independently developed **Claude Code / Codex-style Python coding CLI**.

## Stack

- **Strands Harness + Strands Harness SDK**: one primary model/tool execution loop, context handling, session support and interventions.
- **Amazon Bedrock**: initial language model provider via the Strands-native Bedrock integration.
- **LangChain components**: selective, deterministic helpers for repository documents, splitting, retrieval, Runnables and optional semantic search.
- **Typer + Rich**: terminal UI.
- **Docker/verified sandbox**: isolated command execution added only in a security-gated phase.
- **Deep Agents + LangGraph**: **not** a second MVP runtime. Available later as a measured alternate benchmark or a constrained separately invoked workflow.

Read `docs/HYBRID_INTEGRATION.md` for exact responsibilities and why we avoid double orchestration.

## Milestones

1. Foundation, dependency/API confirmation, offline CLI diagnostics.
2. Bedrock + Strands root loop, policy-checked read-only repo tools, LangChain component adapter.
3. Streaming terminal agent.
4. Staged file diffs and explicit approvals.
5. Isolated build/test execution.
6. Durable sessions, optional LangChain retrieval and MCP, evaluation and tracing.

## How to give this pack to Codex

1. Unzip into a new repository or merge carefully without overwriting existing work.
2. Start Codex in the repository root; it reads `AGENTS.md` automatically. The other Markdown files provide referenced project instructions.
3. Copy the **Phase 0** prompt from `PROMPTS.md` and have Codex implement **only that phase**.
4. Review actual diffs and test evidence in `harness/build-log.md` before asking for Phase 1.
5. Continue phase-by-phase; never instruct the agent to implement the entire roadmap at once.

## Target commands (not implemented by this docs pack)

```bash
uv sync --dev
uv run harness --help
uv run harness doctor
uv run pytest -q
uv run ruff check .
```

## Bedrock setup

Use AWS SSO/profile/IAM role and an accessible model identifier/inference profile. Never store credentials in this repo. Region, model support, streaming and charges depend on your account; validate with an explicit opt-in live smoke test.

Example environment names:

```text
AWS_PROFILE=your-profile
AWS_REGION=your-authorized-region
BEDROCK_MODEL_ID=your-authorized-model-or-inference-profile
```

## Safety note

Strands default file/shell tools run with local process permissions; tool approvals are not filesystem sandboxing. Our root agent starts with **no built-in tools** and registers only audited custom tools. Untrusted commands must not execute on the host. See `docs/SECURITY.md`.

## References

- [Strands Harness](https://strandsagents.com/docs/user-guide/harness/)
- [Composing with Strands SDK](https://strandsagents.com/docs/user-guide/harness/composing-with-sdk/)
- [Strands custom tools](https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools/)
- [LangChain retrieval](https://docs.langchain.com/oss/python/deepagents/retrieval)
- [Deep Agents / LangChain / LangGraph comparison](https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph)

**Status:** documentation pack only, not a built or tested coding agent.
