# CLI reference

## Commands

| Command | Purpose |
| --- | --- |
| `edge-harness` | Chat in the current folder. `--region/--model/--profile` are saved and reused; `--tui`, `--resume ID`, `--show-config`, `--no-login`. Runs `aws login` when credentials are missing/expired (interactive only). |
| `harness doctor` | Local checks only: Python, workspace, AWS settings present, sandbox backend, Git. No AWS call. |
| `harness ask PROMPT` | One streamed turn. Options: `--workspace PATH`, `--project-instructions`, `--skill NAME` (repeatable), `--mcp-config FILE`. |
| `harness chat` | Interactive Rich session. Same options plus `--resume SESSION_ID`. |
| `harness tui` | Textual UI. Options: `--workspace`, `--project-instructions`, `--skill`. Approvals are modal; only the literal reply `yes` approves; `Esc` rejects. TUI slash commands: `/usage`, `/exit`. |

Non-interactive stdin never approves anything.

## Chat slash commands

| Command | Effect |
| --- | --- |
| `/help` | List commands |
| `/new`, `/clear` | Start a new session (same workspace) |
| `/sessions` | List sessions for this workspace |
| `/resume ID` | Switch to a completed session |
| `/compact` | Summarize older messages (one model call; sends them to the provider) |
| `/model [ID]` | Show or switch the Bedrock model; access is checked on the next request |
| `/plan` | Show the agent's recorded task plan |
| `/usage` | Session token totals; cost only when prices are configured |
| `/status` | Workspace and session id |
| `/exit`, `/quit` | Leave |

## Agent tools

Always on: `list_repo`, `read_code`, `search_code`, `find_relevant_code`, `git_status`,
`git_diff`, `update_plan`. With approval: `edit_code`, `delete_code` (diff review), `run_sandboxed`
(command review; only where a sandbox backend exists). Opt-in specialists: `explore_repo`,
`review_changes`, `run_tests`. MCP tools appear with the server name as prefix.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `AWS_REGION`, `AWS_PROFILE` | none | Bedrock region/profile |
| `BEDROCK_MODEL_ID` | none | Model or inference profile id |
| `SANDBOX_WSL_DISTRIBUTION` | `Ubuntu` | WSL distro (Windows) |
| `HARNESS_STATE_DIR` | `~/.edge-harness` | Sessions database and MCP scratch |
| `HARNESS_ENABLE_EXPLORER/REVIEWER/TESTER` | `false` | Opt-in specialists (extra model calls) |
| `INPUT_PRICE_PER_MTOK`, `OUTPUT_PRICE_PER_MTOK` | none | Your own prices for estimates |
