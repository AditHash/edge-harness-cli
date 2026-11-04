# Performance and user experience review

Review period: 2026-11-04 to 2026-11-05. Status: recommendations, not implemented fixes.

## Recommendation

Keep Python for the main agent runtime. Fix discovery, repeated indexing, rendering, database
access, and execution scheduling before considering a rewrite. If profiling still identifies
substantial CPU work afterward, benchmark a narrow Rust retrieval component behind the Python
tool interface. The evidence does not justify a full TypeScript, Rust, or C++ migration today.

## Scope and evidence limits

The review covered the application module inventory (36 modules during the scan), dependency
declarations, scripts, test structure, and architecture documents. Detailed investigation focused
on workspace access, retrieval, streaming, CLI/TUI rendering, sessions, routing, subagents,
sandbox execution, host commands, and Git output. This is a performance review, not a complete
security audit or proof that every code path is correct.

The worktree changed between review turns. A settings-page module appeared after the full scan;
it was not included in the original timing or test results. Findings below were checked against
their relevant code again when this document was written. Existing user changes were preserved.

No billed model calls, cloud mutations, or product-code changes were made for this review.
Local timings are samples from Windows and this checkout, not portable performance guarantees.
Temporary benchmark code was run through standard input; raw benchmark scripts and full traces
were not retained. Commands and methods below describe how to reproduce the observations.

## Measurements

| Check | Observation | Interpretation |
| --- | --- | --- |
| Default discovery at repository root | 500 files; 495 under `.live-smoke`; no `src/` files | Search scope is incorrect in this checkout |
| Root discovery, five samples | Median 1,375.70 ms | Repeated traversal is noticeable |
| Root chunk gathering, five samples | Median 2,843.33 ms; 975 chunks | File access dominates much of retrieval |
| Root ranked query, five samples | Median 3,004.87 ms | Every question repeats corpus preparation |
| Source-folder-only query, one sample | About 136 ms; 36 files, 222 chunks | Different corpus; not an equivalent speedup benchmark |
| Instrumented root retrieval | 4.27 s total; 3.14 s gathering chunks; 1.05 s tokenizing | Profiling adds overhead; these are cumulative timings |
| Session details, 10 / 100 / 500 sessions | 21 / 201 / 1,001 connections | Two reads per session plus the initial listing |
| Session details, 500 sessions | About 0.66 to 1.80 s across two probes | Cost grows with session count and local storage conditions |
| SQLite lifetime probe | Windows rename blocked before garbage collection; succeeded afterward | Transaction contexts do not deterministically close connections |
| Simulated CLI reply, 2,000 deltas | 2,000 Markdown constructions in the original probe | Display FPS does not throttle Markdown construction |
| Simulated TUI reply, 100 deltas | Zero reply log writes before the final usage event | Reply text is buffered rather than visibly streamed |
| Fresh-process module imports, one sample each | Launcher 0.364 s; CLI 1.539 s; retrieval 1.267 s | Includes interpreter startup; measurements overlap and must not be added |

The CLI acquired an additional final render in subsequent worktree changes. The original 2,000
count describes the earlier probe; the current per-delta construction mechanism still exists.

## Findings and proposed changes

### F01 - File discovery can exclude the actual source tree

Priority: high. Evidence: reproduced in this checkout.

Code: [Workspace.files](../../src/code_harness/workspace.py),
[list_repo](../../src/code_harness/agent.py).

Discovery stops at 500 files and does not exclude `.live-smoke`. Sorted traversal consumes the
limit there before reaching `src/`. Exact search warns about its discovery limit, and listing
does too; ranked retrieval has no equivalent completeness indicator.

Add configurable generated-directory exclusions and directory/path scoping. Return explicit
completeness information to all consumers. Git-ignore rules can be an optional usability input,
but must not replace protected-path authorization. Raising the limit alone increases work and
does not address relevance. Verify that a generated directory cannot hide source files and that
truncated retrieval is visibly identified.

### F02 - Ranked retrieval rebuilds the corpus for every question

Priority: high. Evidence: code inspection and timing/profile probes.

Code: [WorkspaceRetriever](../../src/code_harness/retrieval.py).

Each query discovers files, reads them, constructs overlapping chunks, tokenizes their paths and
contents, builds counters, and sorts all matching chunks. Cache chunks, token counts, document
lengths, and corpus statistics; update only changed files. Consider an inverted index once
corpus preparation is cached. Top-k selection is secondary and must preserve overlap filtering
and deterministic ordering.

Keep indexes workspace-scoped. Invalidate creates, edits, deletions, and external changes;
revalidate access before returning cached content. Verify stale hits, links, protected files,
workspace isolation, and retrieval quality before relying on cache speedups.

### F03 - CLI Markdown construction runs on every text delta

Priority: high for responsiveness. Evidence: simulated stream and code inspection.

Code: [_ReplyView](../../src/code_harness/cli.py).

The 8 FPS Rich refresh setting limits redraw cadence, but `add()` constructs Markdown from the
entire accumulated reply on each delta. Batch parsing at a bounded cadence, render the latest
buffer, and flush on completion and before tool/approval transitions. Long replies may benefit
from freezing completed blocks. Verify long code fences, tables, nonterminal output, and final
text fidelity. Measure parsing time and construction count, not just terminal redraw count.

### F04 - TUI reply buffering delays visible output

Priority: high for perceived latency. Evidence: simulated stream and current code.

Code: [HarnessApp.run_turn](../../src/code_harness/tui.py).

Text deltas accumulate until a later non-text event or stream completion. Update an active
reply widget periodically so users see progress during generation. Reasoning also repeatedly
sums the buffered parts' lengths; replace this with an incrementing character count and throttle
cross-thread status updates. Verify that a delayed fake stream becomes visible before completion
and that final output remains complete.

### F05 - Session listing scales poorly and connection closure is deferred

Priority: medium to high for long-lived installations. Evidence: connection counts, timings,
and Windows file-lock probe.

Code: [SQLiteStorage and Sessions](../../src/code_harness/sessions.py).

Listing reads each workspace-owner blob, then each matching metadata blob. Older sessions can
require additional snapshot-prefix checks. Prefix listing uses `substr(key, ...)`, which should
be evaluated with `EXPLAIN QUERY PLAN` rather than assuming efficient indexed prefix lookup.

Fetch ownership and metadata in batches, or introduce indexed session metadata with pagination.
Use a direct latest-session query. Explicitly close connections with a closing context or an
owned lifecycle; SQLite's transaction context only manages transactions. Consider WAL and
connection reuse only after measuring contention and defining thread ownership. Verify listing
across multiple workspaces, legacy sessions, concurrent saves, and deterministic handle release.

### F06 - Long commands monopolize the shared approval gate

Priority: medium. Evidence: lock scope in code; concurrent timing not measured.

Code: [run_sandboxed](../../src/code_harness/agent.py),
[run_on_host](../../src/code_harness/host.py).

The shared approval lock remains held while commands execute. This blocks other approval
requests and serializes isolated sandbox commands despite parallel subagent slots. Web fetch
already releases the gate after approval.

Separate prompt serialization from execution scheduling. Preserve edit conflict protection,
dependent-command ordering, cancellation checks, and appropriate serialization for host mutations.
Only independent approved sandbox jobs should overlap. Verify that cancellation or denial cannot
start queued work and that terminal approvals never race.

### F07 - Output limits apply after full host/Git output collection

Priority: medium for memory use and progress visibility. Evidence: code inspection.

Code: [run_command](../../src/code_harness/host.py),
[_run](../../src/code_harness/git_tools.py).

Host execution uses `communicate()` and Git uses captured subprocess output. Both truncate after
collection, so the returned text limit does not bound capture memory. Drain pipes continuously
into bounded buffers, retain a useful tail, label truncation, and optionally show throttled
progress. Avoid backpressure deadlocks; retain real exit codes and timeout/cancellation behavior.
Verify with high-volume stdout/stderr. No memory stress benchmark was run during this review.

### F08 - Sandbox commands repeatedly build and transfer full snapshots

Priority: investigate after retrieval and rendering. Evidence: code inspection.

Code: [_snapshot and execute_in_sandbox](../../src/code_harness/sandbox.py),
[local MCP connection](../../src/code_harness/mcp_integration.py).

Each command discovers and reads files, builds a tar archive, base64-encodes it into JSON, starts
the helper, and extracts a fresh copy. Local MCP builds a snapshot on connection. These stages
have not been timed separately. Instrument archive creation, transfer, helper startup, extraction,
and actual command execution. Reuse validated snapshot data only with reliable invalidation and
fresh execution isolation. Do not introduce writable host mounts or remove policy checks.

### F09 - Routing preferences do not optimize measured task latency

Priority: investigate; end-to-end impact unmeasured.

Code: [candidate selection](../../src/code_harness/catalog.py),
[RoutedModel](../../src/code_harness/routing.py).

Candidates favor residency tier and larger context windows. These are useful constraints, but
do not establish which model finishes a coding task fastest. Add an optional speed preference
based on successful-call latency, time to first visible token, tool reliability, and task quality.
Keep residency and context eligibility mandatory. Measure retry/failover latency and compaction
frequency: the router currently compacts against the smallest eligible candidate window. Any
change must preserve safe fallback capacity. No model ranking or provider speed claim was
validated by this offline review.

### F10 - TUI cancellation and input handling need improvement

Priority: medium for usability. Evidence: code inspection.

Code: [HarnessApp](../../src/code_harness/tui.py),
[stream_turn](../../src/code_harness/streaming.py).

Ctrl+C quits the application; the TUI does not pass an explicit turn cancellation signal to
`stream_turn`. Input is cleared before the busy check, so submitting during work loses the typed
message. Add a cancel-turn action that keeps the session available and propagates cancellation
to tools/subagents. Retain unsent input or provide an explicit queue. Verify behavior during
generation, approval, sandbox execution, and shutdown.

### F11 - Startup deserves targeted dependency profiling

Priority: medium for short commands; evidence limited to import samples and code inspection.

Code: [CLI imports](../../src/code_harness/cli.py),
[agent composition](../../src/code_harness/agent.py),
[retrieval](../../src/code_harness/retrieval.py), [dependencies](../../pyproject.toml).

CLI imports bring in session/runtime dependencies before a command executes. Retrieval imports
LangChain, while the exact-search adapter wraps a plain function in `RunnableLambda`. Measure
import trees and command startup, then consider lazy imports and simpler adapters where they
provide equivalent behavior. The samples do not isolate LangChain's cost or justify removing the
framework. Strands currently owns agent construction, session hooks, tools, and conversation
management; a rewrite would require replacing or bridging those integrations.

## Language migration assessment

| Strategy | Potential benefit | Decision |
| --- | --- | --- |
| Optimize Python | Avoid repeated work, reduce I/O, improve rendering and scheduling | Recommended first |
| Python plus Rust retrieval | Faster remaining CPU-heavy tokenization/indexing | Prototype only after caching and profiling |
| Full Rust runtime | Potential startup, packaging, memory, and CPU improvements | Requires explicit product goals and equivalent-behavior benchmarks |
| TypeScript/Node.js | Could fit a future web-oriented interface and shared application stack | No demonstrated performance case for migration |
| C++ | Native CPU processing and integration with an existing C++ stack | No measured reason to prefer it for this repository |

Changing local implementation language does not directly speed remote model generation. The
share of real task latency spent in inference has not been measured. No numeric rewrite speedup
is supported. In the profiled query, eliminating tokenization entirely would remove only about
one quarter of that sample's time; it would not eliminate repeated file gathering.

Preserve handle-based file authorization, stale-diff detection, platform-specific edit behavior,
approval semantics, session restore, event ordering, and cancellation through any migration.
Benchmark Rust through a narrow Python interface using identical corpora and result checks before
committing to native packaging. Node.js also requires avoiding blocking event-loop work.

References: [SQLite connection contexts](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager),
[Node.js event-loop guidance](https://nodejs.org/en/learn/asynchronous-work/dont-block-the-event-loop).
These explain runtime behavior; they are not migration benchmarks.

## Recommended implementation order and measurement gates

1. Fix discovery and completeness reporting; confirm intended source coverage.
2. Add incremental retrieval and verify unchanged-query reuse plus safe invalidation.
3. Batch CLI rendering and visibly stream TUI replies; measure first visible output and UI cost.
4. Batch session queries and close connections; benchmark 10, 100, and 500 sessions.
5. Separate approval gates from safe execution scheduling; test cancellation and ordering.
6. Bound process output; profile sandbox preparation and startup before optimizing transport.
7. Collect opt-in model/task latency data; evaluate routing preferences and compaction tradeoffs.
8. Benchmark a Rust component only if significant CPU work remains.

Use separate cold/warm measurements, fixed corpora and output sizes, multiple samples, and p50/p95
latencies. Record CPU time, memory, file reads, database connections, rendering count, and tool
failure rates. Treat source-only versus root timings as different workloads. Suggested latency
targets should be agreed after a representative baseline; no target or projected speedup here
has been achieved by implementation.

## Verification record and reproduction

Earlier focused review run: **36 passed** across retrieval, sessions, streaming, TUI, and CLI UI.
Broader scan run: **361 passed, 14 skipped in 83.12 seconds**; Ruff reported **All checks passed!**
These results belong to the worktree at scan time, before later settings-page changes, and are
not a certification of the current moving worktree. Skipped platform/opt-in tests are not passes.
Live model timing, real terminal behavior, native POSIX behavior, and opt-in sandbox/MCP
integration were not newly verified for this review. Benchmark temporary-directory cleanup twice
failed with Windows file locks; those probe processes exited nonzero despite printing timings.
The later garbage-collection/rename probe isolated the connection-lifetime behavior.

Commands used from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m pytest -q tests/test_retrieval.py tests/test_sessions.py tests/test_streaming.py tests/test_tui.py tests/test_cli_ui.py
```

Reproduce local probes without model calls:

- Count `Workspace(Path.cwd()).files()` by its first path component; check source coverage.
- Time `files()`, `WorkspaceRetriever._chunks()`, and `invoke()` with `time.perf_counter()`.
  Use five samples for medians; use `cProfile` separately for attribution.
- Seed a disposable SQLite database with owner and metadata rows; wrap `sqlite3.connect` to count
  calls during `Sessions.details()`. Close retained connections before cleanup.
- Feed simulated text events to CLI/TUI adapters; count Markdown construction and visible writes
  before a delayed stream completes.
- Measure fresh interpreter subprocesses importing each module. Report process startup inclusion
  and avoid attributing total import time to one transitive dependency.

All findings remain proposals until implemented and verified against these behaviors.
