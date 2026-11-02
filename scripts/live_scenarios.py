"""Opt-in LIVE Bedrock run of the approval-gated scenarios (B edit, C debug, F MCP).

This makes real, billed model calls. Run only with authorization:

    $env:AWS_REGION='ap-south-1'; $env:BEDROCK_MODEL_ID='...'
    uv run python scripts/live_scenarios.py

Approvals are granted by this script instead of a person, and every reviewed diff, command and
MCP call is printed so the run can be audited. Workspaces live in ./.live-smoke (gitignored).
"""

import asyncio
import shutil
import sys
from pathlib import Path

from code_harness.agent import create_agent
from code_harness.config import Settings
from code_harness.streaming import stream_turn

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / ".live-smoke"
SHOP = ROOT / "tests" / "fixtures" / "shop_repo"


def fresh(name: str) -> Path:
    path = BASE / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def approve(kind: str, log: list[str]):
    def review(item) -> bool:
        text = getattr(item, "diff", item)
        log.append(f"{kind}: {text}")
        print(f"\n--- APPROVED {kind} ---\n{text}\n---")
        return True

    return review


async def run_turn(agent, prompt: str) -> tuple[str, list[str]]:
    text, tools = [], []
    async for event in stream_turn(agent, prompt):
        if event.kind == "text_delta":
            text.append(event.text)
        elif event.kind in {"tool_started", "usage", "error"}:
            tools.append(f"{event.kind}: {event.text}")
    return "".join(text), tools


def scenario_b() -> bool:
    root = fresh("scenario_b")
    shutil.copytree(SHOP, root, dirs_exist_ok=True)
    log: list[str] = []
    agent = create_agent(Settings(workspace=root), review=approve("edit", log))
    reply, tools = asyncio.run(
        run_turn(agent, "In shop/cart.py change the bulk discount threshold from 10 to 5. Edit it.")
    )
    print(reply, *tools, sep="\n")
    changed = "BULK_THRESHOLD = 5" in (root / "shop" / "cart.py").read_text()
    return changed and len(log) >= 1


def scenario_c() -> bool:
    root = fresh("scenario_c")
    (root / "calc.py").write_text("def mean(values):\n    return sum(values) / (len(values) + 1)\n")
    (root / "test_calc.py").write_text(
        "import unittest\nfrom calc import mean\n\n\nclass T(unittest.TestCase):\n"
        "    def test_mean(self):\n        self.assertEqual(mean([2, 4]), 3)\n"
    )
    log: list[str] = []
    agent = create_agent(
        Settings(workspace=root),
        review=approve("edit", log),
        review_command=approve("command", log),
    )
    reply, tools = asyncio.run(
        run_turn(
            agent,
            "Run `python3 -m unittest -q` with run_sandboxed. It fails. Fix the bug in the code "
            "(not the test), rerun the tests, and report the real final result.",
        )
    )
    print(reply, *tools, sep="\n")
    fixed = "len(values) + 1" not in (root / "calc.py").read_text()
    commands = [entry for entry in log if entry.startswith("command")]
    return fixed and len(commands) >= 2


def scenario_f() -> bool:
    from code_harness.mcp_integration import MCPServerConfig, connect_sandboxed_mcp
    from code_harness.workspace import Workspace

    root = fresh("scenario_f")
    fixture = ROOT / "tests" / "fixtures" / "mcp_fixture_server.py"
    (root / "server.py").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    log: list[str] = []
    config = MCPServerConfig("fixture", ("python3", "/workspace/server.py"))
    with connect_sandboxed_mcp(Workspace(root), config, BASE / "mcp-state") as (tools, _):
        agent = create_agent(
            Settings(workspace=root),
            mcp_tools=tools,
            mcp_review=lambda name, args: approve("mcp", log)(f"{name} {args}"),
        )
        reply, events = asyncio.run(
            run_turn(
                agent, "Call the fixture probe tool once and tell me exactly what it returned."
            )
        )
    print(reply, *events, sep="\n")
    return len(log) == 1 and "isolated" in reply


def scenario_tui() -> bool:
    """Headless Textual pilot: a real streamed turn renders and usage is tracked."""
    from code_harness.tui import HarnessApp

    root = fresh("scenario_tui")
    app = HarnessApp(lambda display, read_reply: create_agent(Settings(workspace=root)))

    async def drive() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"Reply with the single word ok.", "enter")
            for _ in range(600):
                await pilot.pause(0.1)
                if app.tracker.turns:
                    break
            await pilot.pause(0.3)

    asyncio.run(drive())
    print(app.tracker.summary())
    return app.tracker.turns == 1 and app.tracker.output_tokens > 0


def main() -> int:
    results = {}
    chosen = set(sys.argv[1:]) or {"B", "C", "F", "TUI"}
    scenarios = (("B", scenario_b), ("C", scenario_c), ("F", scenario_f), ("TUI", scenario_tui))
    for name, scenario in [item for item in scenarios if item[0] in chosen]:
        print(f"\n===== Scenario {name} =====")
        try:
            results[name] = scenario()
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"Scenario {name} raised {type(exc).__name__}")
            results[name] = False
    print("\nRESULTS:", results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
