import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_harness import launcher


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    for variable in ("AWS_REGION", "BEDROCK_MODEL_ID", "AWS_PROFILE", "HARNESS_RESIDENCY"):
        # setenv first so teardown restores the original state even though the launcher
        # writes os.environ directly.
        monkeypatch.setenv(variable, "")
        monkeypatch.delenv(variable)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_options_are_saved_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    result = runner.invoke(
        launcher.launcher,
        ["--region", "ap-south-1", "--model", "in.anthropic.claude-haiku-4-5", "--show-config"],
    )
    assert result.exit_code == 0, result.output
    saved = json.loads(launcher.settings_path().read_text())
    assert saved == {"model": "in.anthropic.claude-haiku-4-5", "region": "ap-south-1"}
    for variable in ("AWS_REGION", "BEDROCK_MODEL_ID"):
        monkeypatch.delenv(variable, raising=False)
    again = runner.invoke(launcher.launcher, ["--show-config"])
    assert "ap-south-1" in again.output and "claude-haiku-4-5" in again.output


def test_environment_overrides_saved_value(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher.save({"region": "ap-south-1"})
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert launcher.apply_to_environment(launcher.load_saved())["region"] == "us-east-1"


@pytest.mark.parametrize(
    ("key", "value"),
    [("region", "x; rm -rf /"), ("model", "--evil"), ("profile", "a b"), ("secret", "x")],
)
def test_invalid_values_are_refused(key: str, value: str) -> None:
    with pytest.raises(Exception):  # noqa: B017 - typer.BadParameter
        launcher.save({key: value})
    assert not launcher.settings_path().exists()


def test_tampered_settings_file_is_ignored() -> None:
    launcher.state_dir().mkdir(parents=True)
    launcher.settings_path().write_text(json.dumps({"region": "bad region", "token": "x"}))
    assert launcher.load_saved() == {}


def test_missing_configuration_stops_before_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: pytest.fail("checked"))
    result = CliRunner().invoke(launcher.launcher, [])
    assert result.exit_code == 1 and "Not configured" in result.output


def test_login_runs_only_when_needed_and_interactive() -> None:
    calls: list[str] = []
    states = iter([False, True])

    def check(profile, region) -> bool:
        calls.append("check")
        return next(states)

    def login(profile, region) -> bool:
        calls.append(f"login {profile} {region}")
        return True

    assert launcher.ensure_credentials("p", "r", interactive=True, check=check, login=login)
    assert calls == ["check", "login p r", "check"]

    calls.clear()
    assert launcher.ensure_credentials(
        None, None, interactive=True, check=lambda *a: True, login=login
    )
    assert calls == []

    assert not launcher.ensure_credentials(
        None, None, interactive=False, check=lambda *a: False, login=login
    )
    assert calls == []


def test_failed_login_does_not_start_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher.save({"region": "ap-south-1", "model": "m1"})
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: False)
    from code_harness import cli

    monkeypatch.setattr(cli, "chat", lambda **k: pytest.fail("chat started"))
    assert CliRunner().invoke(launcher.launcher, []).exit_code == 1


def test_valid_credentials_start_chat_in_current_folder(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    launcher.save({"region": "ap-south-1", "model": "m1"})
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    from code_harness import cli

    started: dict = {}
    monkeypatch.setattr(cli, "chat", lambda **k: started.update(k))
    result = CliRunner().invoke(launcher.launcher, ["--resume", "abc"])
    assert result.exit_code == 0, result.output
    assert started["workspace"] == isolated and started["resume"] == "abc"


def test_aws_login_reports_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.aws_login(None, None) is False


def test_auto_model_stays_routed_and_forces_mumbai_region(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    import os

    from code_harness import catalog, cli

    launcher.save({"region": "us-east-1", "model": "auto", "residency": "india"})
    chosen = catalog.CatalogModel(
        "in.anthropic.claude-haiku-4-5", "india", "anthropic", 200_000, "sdk", "ok"
    )
    seen: dict = {}

    def fake_resolve(state, tiers, notify):
        seen["tiers"] = tuple(tiers)
        return chosen

    def fake_chat(**kwargs):
        seen.update(
            model=os.environ["BEDROCK_MODEL_ID"],
            region=os.environ["AWS_REGION"],
            residency=os.environ["HARNESS_RESIDENCY"],
        )

    monkeypatch.setattr(catalog, "resolve_auto", fake_resolve)
    monkeypatch.setattr(catalog, "routing_candidates", lambda state, tiers: [chosen])
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    monkeypatch.setattr(cli, "chat", fake_chat)
    result = CliRunner().invoke(launcher.launcher, [])
    assert result.exit_code == 0, result.output
    assert seen == {
        "tiers": ("india",),
        "model": "auto",
        "region": "ap-south-1",
        "residency": "india",
    }
    assert "Auto routing (per request, with failover)" in result.output


def test_auto_without_capable_model_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness import catalog, cli

    launcher.save({"model": "auto"})
    monkeypatch.setattr(catalog, "resolve_auto", lambda *a, **k: None)
    monkeypatch.setattr(catalog, "routing_candidates", lambda *a, **k: [])
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    monkeypatch.setattr(cli, "chat", lambda **k: pytest.fail("chat started"))
    result = CliRunner().invoke(launcher.launcher, [])
    assert result.exit_code == 1 and "no tool-capable model" in result.output


def test_invalid_residency_refused() -> None:
    result = CliRunner().invoke(launcher.launcher, ["--residency", "usa", "--show-config"])
    assert result.exit_code != 0


def test_manual_prints_without_config_or_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: pytest.fail("aws"))
    result = CliRunner().invoke(launcher.launcher, ["--manual"])
    assert result.exit_code == 0 and "/changes" in result.output
    assert not launcher.settings_path().exists()


def test_manual_only_lists_commands_that_exist() -> None:
    import re

    from code_harness import cli
    from code_harness.manual import MANUAL

    help_text = CliRunner().invoke(launcher.launcher, ["--help"], env={"COLUMNS": "200"}).output
    for flag in set(re.findall(r"edge-harness (--[a-z-]+)", MANUAL)):
        assert flag in help_text, flag
    source = Path(cli.__file__).read_text(encoding="utf-8")
    for command in set(re.findall(r"^  (/[a-z]+(?: ask)?)", MANUAL, flags=re.MULTILINE)):
        assert f'"{command}"' in source or f'"{command} "' in source, command


def test_guidance_and_mcp_options_reach_chat(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    from code_harness import cli

    launcher.save({"region": "ap-south-1", "model": "m1"})
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    started: dict = {}
    monkeypatch.setattr(cli, "chat", lambda **k: started.update(k))
    config = isolated / "server.json"
    result = CliRunner().invoke(
        launcher.launcher,
        ["--skill", "a", "--skill", "b", "--project-instructions", "--mcp-config", str(config)],
    )
    assert result.exit_code == 0, result.output
    assert started["skill"] == ["a", "b"]
    assert started["project_instructions"] is True
    assert started["mcp_config"] == config


def test_tui_receives_resume_and_refuses_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness import cli

    launcher.save({"region": "ap-south-1", "model": "m1"})
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    started: dict = {}
    monkeypatch.setattr(cli, "tui", lambda **k: started.update(k))
    result = CliRunner().invoke(launcher.launcher, ["--tui", "--resume", "abc", "--skill", "s"])
    assert result.exit_code == 0, result.output
    assert started["resume"] == "abc" and started["skill"] == ["s"]
    refused = CliRunner().invoke(launcher.launcher, ["--tui", "--mcp", "x"])
    assert refused.exit_code == 1 and "chat" in refused.output


def test_tui_resume_uses_that_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness import cli, tui
    from code_harness.sessions import Sessions

    workspace = tmp_path / "repo"
    workspace.mkdir()
    state = tmp_path / "state"
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    session_id = Sessions(state / "sessions.sqlite", workspace).create()
    used: list[str] = []

    class FakeApp:
        def __init__(self, make_agent, tracker, on_turn=None, on_usage=None):
            make_agent(print, input)

        def run(self):
            pass

    monkeypatch.setattr(tui, "HarnessApp", FakeApp)
    monkeypatch.setattr(
        cli,
        "_make_agent",
        lambda root, manager, guidance, review_io=None: used.append(manager.session_id),
    )
    cli.tui(workspace=workspace, project_instructions=False, skill=None, resume=session_id)
    assert used == [session_id]


def test_continue_resumes_latest_or_starts_new(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    from code_harness import cli
    from code_harness.sessions import Sessions

    launcher.save({"region": "ap-south-1", "model": "m1"})
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    started: dict = {}
    monkeypatch.setattr(cli, "chat", lambda **k: started.update(k))
    (isolated / "repo").mkdir()
    monkeypatch.chdir(isolated / "repo")
    fresh = CliRunner().invoke(launcher.launcher, ["--continue"])
    assert "No earlier session" in fresh.output and started["resume"] is None
    sessions = Sessions(launcher.state_dir() / "sessions.sqlite", Path.cwd())
    session_id = sessions.create()
    sessions.record_turn(session_id, "hello")
    assert CliRunner().invoke(launcher.launcher, ["--continue"]).exit_code == 0
    assert started["resume"] == session_id
