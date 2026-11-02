# HYBRID_INTEGRATION.md — Strands Harness + LangChain, without two root loops

## Architectural decision

**Use both ecosystems, but keep one agent orchestrator.** Strands Harness (built on Strands Harness SDK) owns conversation state, tool selection, interruptions, context management, retries and the primary Bedrock model. LangChain is an **in-process component library**, used where its `Document`, loaders, splitters, retrievers, vector stores or `Runnable` primitives offer value. This is a deliberate hybrid, not a claim that `create_deep_agent` can be inserted into `create_harness` directly.

Reference docs:
- https://strandsagents.com/docs/user-guide/harness/
- https://strandsagents.com/docs/user-guide/harness/composing-with-sdk/
- https://strandsagents.com/docs/user-guide/harness/configure/tools-and-instructions/
- https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools/
- https://docs.langchain.com/oss/python/deepagents/retrieval
- https://www.langchain.com/blog/deep-agents-vs-langchain-vs-langgraph

## Boundary diagram

```text
Strands root Agent (Bedrock)
       |
       | tools=[search_code, read_code, propose_patch, run_tests_later]
       v
Strands @tool  -----> Authorize request and normalize path/scope
       |
       +----> RepoSearchService / LangChain Runnable/ Retriever
       |           |
       |           +--> allowed, indexed repository files only
       |           +--> bounded snippets with path:line provenance
       |
       +----> DiffProposer / verified Sandbox
       |
       +----> MCP bridge (native Strands integration, opt-in)
       v
Bounded structured tool result
```

## Component matrix

| Capability | Primary implementation | Why |
|---|---|---|
| Agent tool loop, planning, compaction, sessions | Strands Harness | no duplicated state/loop |
| Bedrock LLM, streaming and tool calls | Strands native Bedrock | direct supported model integration |
| Code search | `ripgrep` / safe Python lexical search initially | cheap, deterministic, exact symbols |
| Optional semantic code retrieval | LangChain document splitting + retriever | add only if retrieval eval improves |
| Deterministic pre/post-processing | LangChain `Runnable` or plain Python | bounded, independently testable functions |
| External tools / skills | Strands native MCP/skills | no duplicate protocol adapter |
| Multi-step deterministic pipelines (later) | LangGraph as a **tool-invoked workflow** | only when business rules demand stateful graph |
| Autonomous specialist (later) | benchmark Deep Agents separately | do not create two competing root harnesses |
| File editing / command approval | our policy + Strands interventions | SDK interrupt is UX, not OS protection |
| Execution isolation | verified OS sandbox | model cannot run host commands |

## Strands tool wraps a LangChain component

The following is **illustrative integration glue, not a complete secure code search**. It demonstrates the boundary without introducing a second agent. The `search_service` is built by our application with a filtered, workspace-scoped index. The service must validate paths **again** before returning passages.

```python
from strands import tool
from strands_harness import create_harness
from langchain_core.runnables import RunnableLambda

# Search returns bounded, redacted snippets with path:line metadata.
# Implement this service in code_harness.langchain_services.code_retrieval.
# It must use only an authorized index, not arbitrary host files.
search_pipeline = RunnableLambda(search_service.search)

@tool
def search_code(query: str) -> str:
    """Search only authorized repository source files for code context."""
    return search_pipeline.invoke(query)

agent = create_harness(
    builtin_tools=[],  # important: no unrestricted built-in shell/read/write/edit
    tools=[search_code],
    # Configure Bedrock explicitly in our runtime factory.
)
```

`search_service` in this architectural example is an injected object, **not** a defined SDK symbol. Codex should replace the snippet with tested code and an isolated fixture. For MVP, do not require vector embeddings. A plain lexical tool may outperform vector search for exact code identifiers.

## Contract for tool adapters

- Input: validated `query`, max result count, workspace-scoped session identity, per-call permissions and cancellation context.
- Output: a bounded typed payload or JSON string with `path`, `start_line`, `end_line`, `excerpt`, and `truncated` flags. Include provenance; never return raw huge files.
- Authorize every hit, including cached/vector-index hits, **at retrieval time**. Reindex/clear index when workspace scope changes. No reads of `.env`, private keys, credentials, git internals or outside-root files.
- Tool errors become structured failures in the agent stream, not fake empty success.
- `Runnable` has no independent permission to invoke the root model, shell, or network. Inject safe dependencies only.
- In Phase 1, compare same-query results for lexical and LangChain adapters against a deterministic fixture; no Bedrock calls in offline tests.

## Why not nest Deep Agents now?

Both Deep Agents and Strands Harness provide planning, tool calling, context management and checkpoint/session concepts. Running both as equal root controllers risks double invocation costs, mismatched interrupts, duplicated memory and unclear authority over tool execution. The safe optional *future* bridge is a scoped specialist tool:

```text
Strands root -> specialist.review(snapshot_id, question) -> structured report
```

If the specialist uses LangGraph or Deep Agents, it receives only an approved snapshot, has its own resource budget, no automatic host writes and no ability to approve itself. Record measurable benefit before adding this dependency.

## Phase 0/1 acceptance criteria

1. No `create_agent`, `create_deep_agent` or LangGraph orchestrator executes as part of the Strands root turn loop.
2. A Strands `@tool` successfully invokes a fake LangChain Runnable, passes query through, and returns a scoped result.
3. Unauthorized search hits and protected paths are filtered in the service and tool adapter.
4. Bedrock model configuration is Strands-native, or offline fake model for tests. The LangChain component does not re-invoke Bedrock for a basic search.
5. Tool output size is bounded, includes provenance, handles errors and has no hidden network calls.
