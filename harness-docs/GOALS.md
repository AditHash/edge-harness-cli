# GOALS.md — Product requirements

## Product vision

A **local-first, terminal-based coding assistant** that reasons about code, navigates repositories, proposes minimal modifications, runs isolated verification, and reports evidence. It should feel like a competent CLI coding companion, not merely a chatbot that returns code snippets.

## Primary user stories

1. As a developer, I can launch the CLI in a trusted repository and ask how a module works; answers cite relevant paths and avoid inventing symbols.
2. As a developer, I can ask for a small bug fix; the agent inspects code and displays a unified diff **before** any change reaches my working tree.
3. As a developer, I can approve or reject every proposed edit, and denial leaves my files untouched.
4. As a developer, I can request tests or a build; commands run in an **isolated** environment and report stdout, stderr, exit code, elapsed time, and any limitations.
5. As a developer, I can stop a long-running agent task, see what happened, and later resume a saved conversation without replaying unsafe side effects.
6. As a developer using Bedrock, I can select an accessible model using AWS-native credentials, with clear errors for permission failures and usage/budget visibility when available.

## MVP (release 0.1)

- `harness doctor` checks Python/environment/AWS configuration, and reports missing prerequisites without exposing secrets.
- `harness chat [--workspace PATH]` starts a session in a chosen repository and communicates with one Bedrock model through Strands Harness.
- Read-only repository browsing (`ls`, `read_file`, `glob`, `grep`) with workspace and secret boundaries.
- Visible token/tool streaming when the integration supports it; graceful fallback when not.
- No writes or shell execution in the first working vertical slice.
- Clear errors, cancellation support, deterministic offline unit tests.

## Release 0.2

- File writes and edits shown as diffs; fail-closed per-operation approval; no silent edits.
- Protected files always blocked; path validation and conflict detection.
- Test execution within an isolated copy/sandbox, not on the host.
- Provenance: which files changed, which tests actually ran, what failed or remains uncertain.

## Release 0.3

- Durable sessions and recovery after interruptions.
- `/help`, `/new`, `/resume`, `/status`, `/diff`, `/model` and optional `/compact` if safely exposed by framework.
- MCP tool discovery behind trust/approval gates, optional skills and subagents.
- Observability and cost summaries; optional opt-in trace export with redaction.

## Non-goals

- Feature-for-feature cloning of proprietary systems.
- Developing an original LLM or training/fine-tuning models.
- Running model-generated shell commands on the host OS.
- Automatic git commits, pushes, PR creation, or destructive git operations.
- Browser UI, mobile app, team/cloud accounts, production hosting or CI agents in MVP.
- Unrestricted network browsing, autonomous package installations or credential access.
- More than one active agent or complex multi-agent orchestration before single-agent evaluations succeed.

## Quality bar

- No secret reads in guarded paths, no workspace escape via traversal or symlinks, no unauthorized edits, no host shell execution.
- Refusal/rejection is testable and does not mutate files.
- Errors are actionable and accurately describe what did and did not run.
- Repeated read-only prompts work without session-state corruption.
- Python and tooling are testable on Windows and Linux; Docker-required tests are conditional and skip clearly when unavailable.
- A realistic bug-fix task can be proposed, reviewed, approved, tested in isolation and summarized with evidence.
