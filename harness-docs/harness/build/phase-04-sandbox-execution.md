# Phase 04 — Isolated command execution and test evidence

## Prerequisite

Approved edits work and threat-model tests pass. Present sandbox isolation design before adding `execute` capability.

## Goal

The agent can run tests/build commands **inside a sandbox** and report actual results without giving arbitrary host execution access.

## Implement

- Abstract `SandboxRunner` interface: start/execute/timeout/cancel/cleanup; typed result with exit code, stdout/stderr excerpt, duration and environment identifier.
- Docker-based runner or proven managed sandbox. Prepare a temporary copied workspace snapshot; do not use an unrestricted writable host bind mount. No Docker socket exposure.
- Non-root user; resource quotas, process cleanup, output limits, no network by default and no AWS/SSH credentials inside sandbox.
- User reviews exact command and sandbox scope before execution unless a narrow, explicitly configured safe policy is established.
- Expose shell execution through a custom Strands tool only after a real OS-isolated sandbox is tested. Never connect the built-in host shell or unrestricted subprocess directly to the model.
- No automatic promotion of changes from the sandbox back to host; ask for a separate approved patch if needed.
- Unit + optional Docker-enabled integration tests for success/failure, timeouts, host isolation, path protection and cancellation.

## Acceptance

- Fixture pytest succeeds with reported exit code 0 and captured results.
- A failing test is clearly marked failed, not verified.
- Sandbox cannot read host home directory, reach external internet by default or change the live project without a reviewed patch.
- Unavailable Docker produces a clear, safe error and no host fallback.
