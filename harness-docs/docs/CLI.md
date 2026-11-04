# CLI reference

## Commands

| Command | Purpose |
| --- | --- |
| `edge-harness` | Chat in the current folder. `--region/--model/--profile` are saved and reused; `--tui`, `--resume ID`, `--show-config`, `--no-login`, `--model auto`, `--residency india|mumbai|any`, `--list-models`, `--probe-models N`. Runs `aws login` when credentials are missing/expired (interactive only). |
| `harness doctor` | Local checks only: Python, workspace, AWS settings present, sandbox backend, Git. No AWS call. |
| `harness ask PROMPT` | One streamed turn. Options: `--workspace PATH`, `--project-instructions`, `--skill NAME` (repeatable), `--mcp-config FILE`. |
| `harness chat` | Interactive Rich session. Same options plus `--resume SESSION_ID`. |
| `harness tui` | Textual UI. Options: `--workspace`, `--project-instructions`, `--skill`. Approvals are modal; only the literal reply `yes` approves; `Esc` rejects. TUI slash commands: `/usage`, `/exit`. |

Non-interactive stdin never approves anything. At an approval prompt type `y` or `yes` (this change only), or `a` or `all` (this and every later change of the same kind - file edits or sandbox commands - for the rest of the chat; diffs are still shown). Anything else, including Enter, rejects. Parallel tool calls are reviewed one at a time. MCP calls always ask and need the word `yes`.

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
| `/context` | Context tokens used vs the model window and the auto-compact point |
| `/changes` | Table of file changes you approved this session (+/- lines) |
| `/approve` | Show standing approvals; `/approve ask` clears them |
| `/usage` | Session token totals; cost only when prices are configured |
| `/status` | Workspace and session id |
| `/exit`, `/quit` | Leave |

## Agent tools

Always on: `list_repo`, `read_code`, `search_code`, `find_relevant_code`, `git_status`,
`git_diff`, `update_plan`. With approval: `replace_in_file` (exact unique snippet, preferred), `edit_code`, `delete_code` (diff review), `run_sandboxed`
(command review; only where a sandbox backend exists). Opt-in specialists: `explore_repo`,
`review_changes`, `run_tests`. MCP tools appear with the server name as prefix.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `AWS_REGION`, `AWS_PROFILE` | none | Bedrock region/profile |
| `BEDROCK_MODEL_ID` | none | Model or inference profile id |
| `BEDROCK_MAX_TOKENS` | `16384` | Output cap per model call (256-65536) |
| `SANDBOX_WSL_DISTRIBUTION` | `Ubuntu` | WSL distro (Windows) |
| `HARNESS_STATE_DIR` | `~/.edge-harness` | Sessions database and MCP scratch |
| `HARNESS_ENABLE_EXPLORER/REVIEWER/TESTER` | `false` | Opt-in specialists (extra model calls) |
| `HARNESS_COMPACT_THRESHOLD` | `0.7` | Auto-compact when input passes this share of the window |
| `HARNESS_RESIDENCY` | `any` | `auto` route scope: india, mumbai, any |
| `HARNESS_SHOW_THINKING` | `true` | Print model reasoning (false: spinner with count) |
| `INPUT_PRICE_PER_MTOK`, `OUTPUT_PRICE_PER_MTOK` | none | Your own prices for estimates |
