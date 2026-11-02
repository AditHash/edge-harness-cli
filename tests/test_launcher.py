import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from code_harness import launcher


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    for variable in ("AWS_REGION", "BEDROCK_MODEL_ID", "AWS_PROFILE"):
        monkeypatch.delenv(variable, raising=False)
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


def test_auto_model_resolves_and_forces_mumbai_region(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    from code_harness import catalog, cli

    launcher.save({"region": "us-east-1", "model": "auto", "residency": "india"})
    chosen = catalog.CatalogModel(
        "in.anthropic.claude-sonnet-5", "india", "anthropic", 1_000_000, "sdk", "ok"
    )
    seen: dict = {}

    def fake_resolve(state, tiers, notify):
        seen["tiers"] = tuple(tiers)
        return chosen

    monkeypatch.setattr(catalog, "resolve_auto", fake_resolve)
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    monkeypatch.setattr(
        cli,
        "chat",
        lambda **k: seen.update(
            model=__import__("os").environ["BEDROCK_MODEL_ID"],
            region=__import__("os").environ["AWS_REGION"],
        ),
    )
    result = CliRunner().invoke(launcher.launcher, [])
    assert result.exit_code == 0, result.output
    assert seen == {
        "tiers": ("india",),
        "model": "in.anthropic.claude-sonnet-5",
        "region": "ap-south-1",
    }
    assert "Auto model: in.anthropic.claude-sonnet-5" in result.output


def test_auto_without_capable_model_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness import catalog, cli

    launcher.save({"model": "auto"})
    monkeypatch.setattr(catalog, "resolve_auto", lambda *a, **k: None)
    monkeypatch.setattr(launcher, "ensure_credentials", lambda *a, **k: True)
    monkeypatch.setattr(cli, "chat", lambda **k: pytest.fail("chat started"))
    result = CliRunner().invoke(launcher.launcher, [])
    assert result.exit_code == 1 and "no tool-capable model" in result.output


def test_invalid_residency_refused() -> None:
    result = CliRunner().invoke(launcher.launcher, ["--residency", "usa", "--show-config"])
    assert result.exit_code != 0
