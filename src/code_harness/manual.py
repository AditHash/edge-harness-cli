"""The command manual printed by `edge-harness --manual`."""

MANUAL = """\
EDGE-HARNESS COMMANDS

Start
  edge-harness                                  chat in the current folder
  edge-harness --tui                            Textual interface
  edge-harness --resume <session-id>            continue a saved session

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
  /approve              show approvals
  /approve ask          ask every time again
  /sessions             list sessions
  /resume <id>          switch session
  /new, /clear          new session
  /status               workspace and session id
  /exit, /quit          leave

At an approval prompt
  yes                   approve this one
  all                   approve the rest of this session (edits and commands separately)
  anything else         reject
"""
