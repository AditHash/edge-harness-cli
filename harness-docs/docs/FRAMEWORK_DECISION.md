# Framework decision — ADR-001: Hybrid composition (provisional)

**Status:** Architecture direction accepted for the prototype; **not performance-validated** (2026-11-02).

## Decision

Build the primary coding-agent loop with **Strands Harness**, Amazon Bedrock native model integration, and a typed set of custom tools. Use the **LangChain component ecosystem** selectively behind `@tool` adapters (retrieval, `Document`, splitters, Runnables). Keep **Deep Agents** available as an alternative baseline in isolated benchmarks, not part of the MVP dependency graph. See `docs/HYBRID_INTEGRATION.md`.

## Reasons

- Strands Harness is a ready-to-customize agent runtime: `create_harness()` returns a plain Strands `Agent` with tools, context handling, sessions, interventions and native Bedrock support.
- Strands allows `builtin_tools=[]` and custom `tools=[...]`, permitting a small audited tool surface rather than exposing permissive default host tools.
- LangChain has broad retrieval/document processing utilities that are useful as ordinary functions/services without adopting a second LLM orchestration loop.
- Deep Agents is itself a harness; stacking it as a second primary controller adds approval/session/state complexity and may increase cost. It is useful as a benchmark or separately scoped future specialist.

## Alternatives

**A. Deep Agents + LangGraph-only:** viable, especially if LangGraph is central; alternate benchmark, not the initial implementation.

**B. Strands-only:** simpler dependency graph; acceptable if lexical code search is sufficient and LangChain retrieval adds no measurable value.

**C. Both as competing agent loops:** rejected for MVP; unclear source of truth for context, budget, tools and approvals.

## Risks and mitigation

- Strands built-in host tools can read or execute with host permissions: disable by default; use our workspace policy and real command sandbox.
- Framework APIs evolve: verify installed packages and pin a tested dependency lock. `create_harness` options and model identifiers are version-sensitive.
- LangChain indexes may leak cached protected source: restrict indexing; recheck authorization on returned hits; test index reuse across workspaces.
- More dependencies increase maintenance: do not add vector DB, LangGraph or Deep Agents until an acceptance test justifies them.
- Tool adapter overhead can distort streaming or break cancellation: contract-test event and error propagation; keep tool outputs small.

## Decision gates (must be measured)

1. Offline Strands tool adapter invokes a LangChain Runnable and returns deterministic expected results.
2. Explicit Bedrock model with verified tool calling/streaming works in an opt-in live test.
3. No host reads outside the repo or secret access; no model shell or writing in Phase 1.
4. Compare lexical and LangChain-enhanced search accuracy, latency and memory on same code fixture before semantic-index adoption.
5. Phase 3 approvals and Phase 4 OS isolation meet `docs/SECURITY.md` regardless of framework.

## Official references

- https://strandsagents.com/docs/user-guide/harness/
- https://strandsagents.com/docs/user-guide/harness/composing-with-sdk/
- https://strandsagents.com/docs/user-guide/harness/configure/tools-and-instructions/
- https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools/
- https://docs.langchain.com/oss/python/deepagents/retrieval
- https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph
