# ARCHITECTURE.md — Hybrid Strands + LangChain coding-agent design

## Runtime ownership (one agent loop)

```text
Developer terminal: harness chat / harness ask / /diff / /resume
                     |
              Typer + Rich CLI
                     |
             Session / event adapter
                     |
        STRANDS HARNESS (primary agent loop)
        - context, sessions, tool calls, interventions
                     |
            Amazon Bedrock model
                     |
         Typed, policy-checked @tool adapters
              /             |                \
    Repository tools    LangChain services   MCP (later)
    (list/read/grep)    retrieval/chunking   opt-in scopes
              \             |                /
               Central permission / sandbox boundary
                     |
           Diff proposal / verified OS sandbox
                     |
              Repository working tree
```

Strands is the **only owner of conversational turns, agent state and tool-calling decisions**. LangChain is a **library of components**, not a second autonomous root agent. Deep Agents / LangGraph may be benchmarked in isolation, and a LangGraph deterministic workflow may later be called as a bounded tool **only** if a demonstrated use case warrants the added complexity.

## Package layout — target, not yet implemented

```text
src/code_harness/
  cli.py                       # Typer entry, output only
  config.py                    # model, region, workspace, budgets
  doctor.py                    # safe config/prerequisite checks
  runtime/
    agent.py                   # create_harness() composition, single root agent
    bedrock.py                 # Strands native Bedrock model construction
    events.py                  # normalized event adapter for Rich UI
    sessions.py                # Strands persistent session lifecycle
    approvals.py               # interruption/adaptor; policy never bypassed
  tools/
    registry.py                # scoped Strands @tool registration
    read_repo.py               # workspace search/list/read via policy
    propose_patch.py           # creates patch proposals; no direct write
    execution.py               # invokes verified sandbox contract (Phase 4)
  langchain_services/
    code_retrieval.py          # OPTIONAL: LangChain splitters/retrievers
    schemas.py                 # optional Document or runnable types
  workspace/
    policy.py                  # canonical paths, sensitive exclusions
    snapshot.py                # tracked file snapshot for conflict check
    diffs.py                   # unified diff + approved safe apply
  execution/
    sandbox.py                 # isolated command runner interface
    docker_runner.py           # no host shell / credentials / network
  integrations/
    mcp.py                     # Strands-native MCP (later)
    git.py                     # fixed-argument, safe read-only inspection
  ui/
    console.py                 # Rich terminal stream
    prompts.py                 # approvals, cancellation
 tests/
```

## Responsibilities

| Layer | Owns | Must NOT own |
|---|---|---|
| Strands Harness | one model loop, context, interventions, session hooks | direct unapproved host shell |
| Bedrock | inference/tool-calling model | filesystem access or approvals |
| LangChain components | document loading, chunking, deterministic retrieval, optional structured Runnable | primary chat loop, Strands state |
| Workspace policy | actual path/secret authorization | prompting or UI wording alone |
| Tool adapters | JSON-in/validated-out boundaries, rate/output limits | policy override or unsafe host shell |
| CLI | display, confirmations, state navigation | agent reasoning or credentials |
| Sandbox runner | OS-level limits and real exit codes | implicit host working-tree writes |

## Event contract

Normalize SDK events into internal `AgentEvent` records: `text_delta`, `tool_started`, `tool_result`, `approval_required`, `approval_decision`, `error`, `turn_completed`, `usage` (when available), and `cancelled`. Preserve event order and clear correlation IDs. Never log raw sensitive tool results.

## Model / region configuration

Set `BEDROCK_MODEL_ID` (explicit user-authorized model or inference profile), `AWS_REGION`, optional `AWS_PROFILE`, workspace root, max steps/usage, and log redaction policy. Use Strands native Bedrock integration; do **not** instantiate LangChain `ChatBedrockConverse` for the root Strands agent. Live Bedrock smoke tests are opt-in and may incur charges.

## Coding workflow

1. User selects a workspace and asks a question.
2. Strands chooses only exposed policy-checked tools.
3. Tool adapters canonicalize paths, redact/disallow protected inputs and return bounded results.
4. Optional LangChain retrieval service supplies ranked source excerpts **from the same authorized workspace**; it does not bypass access policy.
5. Change requested: produce a staged diff; UI asks for review; validation/checksum occurs again immediately before applying.
6. Execution requested: run approved command only in isolated snapshot; show exact actual stdout/stderr and exit code.
7. Save session status without replaying side effects, then summarize verified results and limitations.

## Delegation rule

No Deep Agents in the MVP runtime. If later needed, design a separate `CodeReviewService` as a bounded tool accepting a repository snapshot and returning a structured review; do not give it unapproved host files, freeform shell, or independent authority. Prefer a simple deterministic LangChain Runnable unless multi-step agentic reasoning clearly improves measured results.

## Security

`docs/SECURITY.md` controls release gating. Strands local file/shell tools execute with the host process privileges and are **not** OS isolation. Our initial tools must be custom and workspace restricted. MCP tools, retrieval indexes and secondary workflows must all share the same authorization boundary.

## Implemented layout (2026-11-02)

The "target" tree above is superseded by this flat layout in `src/code_harness/`:

| Module | Role |
| --- | --- |
| `cli.py`, `tui.py` | Typer/Rich and Textual front ends; same agent, events and approval policy |
| `agent.py` | Single composition root: `create_harness()` with guarded tools, hooks, specialists |
| `streaming.py`, `contracts.py`, `usage.py` | Normalized `AgentEvent` stream; cumulative usage |
| `workspace.py`, `proposals.py`, `approvals.py` | Boundary checks at the open handle, immutable diffs, typed approvals |
| `edits.py`, `posix_edits.py` | Reviewed writes: Windows exclusive handles / POSIX pinned directory fds |
| `platforms.py`, `sandbox.py`, `wsl_sandbox_helper.py` | Sandbox backend selection (WSL, native Linux, none) and supervisor |
| `mcp_integration.py`, `mcp_remote.py`, `wsl_mcp_helper.py` | Local sandboxed and remote MCP + approval hook |
| `retrieval.py` | LangChain `BaseRetriever`: chunked BM25 over the guarded workspace |
| `git_tools.py`, `planning.py`, `instructions.py`, `sessions.py`, `config.py` | Read-only Git, plan state, explicit guidance, SQLite snapshots, settings |

Decisions: LangGraph and Deep Agents are not used. The only workflow that needed more than one
step (plan, delegate, verify) is expressed as Strands tools and agent-as-tool specialists, so a
second orchestrator would add a competing loop without a measured benefit. LangChain is used for
the `BaseRetriever` interface; the benchmark in `tests/test_retrieval.py` shows ranked retrieval
finding the right file for 10/10 natural-language questions where the existing phrase search found
0/10 (queries written by the implementer, so treat the rate as an upper bound).
