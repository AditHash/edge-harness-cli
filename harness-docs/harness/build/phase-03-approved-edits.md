# Phase 03 — Diff review and approved edits

## Prerequisite

Phases 00–02 complete; read `docs/SECURITY.md` and show a short patch/permission design before coding.

## Goal

The agent can propose changes to project files, and **nothing is applied to the working tree until the exact diff is reviewed and explicitly approved**.

## Implement

- A staged change/proposal type: proposal ID, target path(s), base hash(es), proposed content/diff, creation timestamp and approval status.
- Unified diff preview with syntax/context enough to review. Group multiple file changes atomically where possible.
- Fail-closed CLI approval for exact proposed actions; `N`, EOF, error or interruption rejects by default.
- Strands Harness interventions for approval UX where appropriate; independent policy enforcement must protect all custom tools and backend APIs.
- Explicit allow/deny paths with canonicalization, symlink/junction handling and no cross-workspace writes. Guard `.env*`, private keys, `.git` internals and user overrides.
- Recompute expected file state at apply; stale patches reject safely; log only redacted metadata. Keep a recovery path for failure partway through an approved multi-file operation.
- Tests for refusal, approval, stale conflict, traversal, symlink, protected paths and interruption/resume.

## Acceptance

- A normal small edit is previewed and only then applied on approval.
- Reject leaves bytes unchanged; malicious/protected paths stay untouched.
- Editing a file after preview causes a conflict, never an overwrite.
- A denied operation cannot be retried through another tool to bypass approval.

## Out of scope

Shell execution, automatically installing dependencies, git commit/push, autonomous undo of unrelated edits.
