# Phase 01 — Bedrock + Strands Harness read-only coding vertical slice

## Prerequisite

Phase 0 verified. Read `docs/HYBRID_INTEGRATION.md` and verify installed SDK APIs, tool registration, default model provider, and Bedrock invocation capabilities in `docs/SDK_NOTES.md`.

## Goal

One **Strands Harness root agent**, powered by Amazon Bedrock, can search and explain a selected repository without any write or shell capability. A LangChain component can be invoked as a **plain service** through a Strands `@tool` adapter, without a second agent loop.

## Implement

1. `runtime/bedrock.py`: Strands-native Bedrock model built from `BEDROCK_MODEL_ID`, `AWS_REGION`, optional `AWS_PROFILE`; standard credential provider chain, no hardcoded model. Validate exact SDK parameter names against installed version.
2. `runtime/agent.py`: `create_harness(model=..., builtin_tools=[], tools=[list_repo, read_code, search_code], ...)`; avoid built-in unrestricted `read`, `shell`, `write`, and `edit` tools. Confirm final registered tool names with a test.
3. `workspace/policy.py` and `tools/read_repo.py`: safe canonical root, protected paths, symlinks/junctions, file-size/output limits and bounded search. Reject arbitrary absolute/outside-root paths at the trusted boundary.
4. `langchain_services/code_retrieval.py`: a minimal, deterministic optional `Runnable` over an in-memory **approved fixture** or a safe source search. Show Strands `@tool` invoking it and returning structured citations. No embeddings or vector database in the initial slice.
5. Add `harness ask "Explain auth.py"`, offline mocked model/tool tests, and an opt-in live Bedrock smoke test with cost warning.

## Acceptance tests

- Can identify and explain code in a test repository with path/line references.
- `.env*`, private keys, parent directory traversal, symlink/junction escapes and out-of-root reads denied even when the model explicitly requests them.
- No file writes or shell tool registered; refusal test leaves workspace unchanged.
- LangChain Runnable integration works through a Strands tool with no LangGraph/Deep Agents loop and no model invocation from the retriever.
- Missing Bedrock model authorization/region yields actionable error; no silent fallback.

## Caution

`builtin_tools=[]` controls built-in registration; it is **not OS confinement**. Custom tools still run with the process privileges. Keep the fixture disposable for initial tests and enforce policy independently.
