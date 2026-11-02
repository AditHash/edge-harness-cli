import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from strands.models import Model

from code_harness.agent import create_agent
from code_harness.approvals import review_command
from code_harness.config import Settings
from code_harness.tui import ApprovalScreen, HarnessApp


def _model(tool_input: dict[str, str] | None):
    calls = 0

    async def stream(messages, *args, **kwargs):
        nonlocal calls
        calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if tool_input is not None and calls == 1:
            start = {"toolUseId": "t1", "name": "run_sandboxed"}
            yield {"contentBlockStart": {"start": {"toolUse": start}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(tool_input)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "hello from model"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        usage = {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10}
        yield {"metadata": {"usage": usage, "metrics": {"latencyMs": 1}}}

    model = MagicMock(spec=Model)
    model.stateful = False
    model.get_config.return_value = {}
    model.estimate_utilization.return_value = 0.0
    model.stream.side_effect = stream
    return model


def _app(tmp_path: Path, model) -> HarnessApp:
    def make_agent(display, read_reply):
        def review(command: str) -> bool:
            return review_command(command, interactive=True, display=display, read_reply=read_reply)

        return create_agent(Settings(workspace=tmp_path), model=model, review_command=review)

    return HarnessApp(make_agent)


def test_streamed_turn_renders_and_tracks_usage(tmp_path: Path) -> None:
    app = _app(tmp_path, _model(None))

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"hi", "enter")
            for _ in range(100):
                await pilot.pause(0.05)
                if app.tracker.turns:
                    break
            await pilot.pause(0.2)

    asyncio.run(run())
    assert (app.tracker.input_tokens, app.tracker.output_tokens) == (7, 3)


def test_modal_approval_rejects_anything_but_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import code_harness.agent as agent_module
    from code_harness import sandbox
    from code_harness.workspace import WorkspaceError

    ran: list[str] = []

    def fake_execute(workspace, command, review, **kwargs):
        if not review(command):
            raise WorkspaceError("Command rejected; nothing was executed")
        ran.append(command)
        return sandbox.SandboxResult(0, "", "", "completed", 0.0, 0)

    monkeypatch.setattr(agent_module, "sandbox_backend", lambda: "linux")
    monkeypatch.setattr(sandbox, "execute_in_sandbox", fake_execute)
    for reply in ("no", "yes"):
        app = _app(tmp_path, _model({"command": "true"}))

        async def run(reply=reply, app=app) -> None:
            async with app.run_test() as pilot:
                await pilot.click("#prompt")
                await pilot.press(*"go", "enter")
                for _ in range(100):
                    await pilot.pause(0.05)
                    if isinstance(app.screen, ApprovalScreen):
                        break
                assert isinstance(app.screen, ApprovalScreen)
                await pilot.press(*reply, "enter")
                for _ in range(200):
                    await pilot.pause(0.05)
                    if app.tracker.turns:
                        break

        asyncio.run(run())
        assert ran == (["true"] if reply == "yes" else [])
