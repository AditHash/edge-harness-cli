"""The command manual printed by `edge-harness --manual`."""

MANUAL = """\
EDGE-HARNESS COMMANDS

Start
  edge-harness                                  chat in the current folder
  edge-harness --tui                            Textual interface
  edge-harness --resume <session-id>            continue a saved session
  edge-harness --continue                       resume the latest session in this folder
  edge-harness --skill <name>                   load .agents/skills/<name>/SKILL.md (repeatable)
  edge-harness --project-instructions           load AGENTS.md as guidance
  edge-harness --mcp-config <file.json>         connect the MCP server in this file (chat only)
  edge-harness --mcp <name>                     connect a saved MCP server (repeatable)
  edge-harness --mcp-add <file.json>            save an MCP server
  edge-harness --mcp-remove <name>              forget a saved MCP server
  edge-harness --mcp-list                       list saved MCP servers

Model
  edge-harness --model auto                     route per request, with failover (saved)
  edge-harness --model <model-id>               fixed model (saved)
  edge-harness --residency india                auto uses in.* models only (saved)
  edge-harness --residency mumbai               auto uses ap-south-1 models only (saved)
  edge-harness --residency any                  India first, then Mumbai (saved)
  edge-harness --list-models                    list models and exit
  edge-harness --probe-models <n>               test tools on n models (small billed calls)

Config
  edge-harness --region <region>                save AWS region
  edge-harness --profile <name>                 save AWS profile
  edge-harness --show-config                    print saved settings
  edge-harness --no-login                       skip the credential check
  edge-harness --manual                         print this manual
  edge-harness --help                           short option list

Inside chat
  /help                 list commands
  /model                show model and route
  /model <id>           switch model (/model auto works too)
  /context              context used vs window
  /usage                session token totals
  /compact              summarize older messages
  /plan                 show task plan
  /changes              files changed this session
  /image <path> [text]  attach an image to your next message
  /skills               list available skills
  /skill <name>         load a skill into this chat
  /mcp                  saved MCP servers and connection state
  /mcp connect <name>   connect a saved MCP server
  /cost                 estimated session cost by model
  /approve              show approvals
  /approve ask          ask every time again
  /sessions             list sessions
  /resume <id>          switch session
  /new, /clear          new session
  /status               workspace and session id
  /exit, /quit          leave

At an approval prompt
  y, yes                approve this one
  a, all                approve the rest of this session (edits and commands separately)
  anything else         reject (n, or just Enter)
"""
