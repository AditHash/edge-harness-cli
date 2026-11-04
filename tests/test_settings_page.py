import asyncio
import json
import os
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from code_harness import cli, launcher
from code_harness.config import Settings
from code_harness.settings_page import SPECS, SettingsApp, model_choices, settings_table

ALL_VARIABLES = [variable for variable, _ in launcher.FIELDS.values()]


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")
    for variable in ALL_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return tmp_path


def test_every_field_is_on_the_page_and_the_reverse() -> None:
    assert set(SPECS) == set(launcher.FIELDS)


@pytest.mark.parametrize(
    "key, value",
    [
        ("max_tokens", "16384"),
        ("compact_threshold", "0.5"),
        ("session_budget", "2.5"),
        ("input_price", "3.3"),
        ("subagent_turn_tokens", "0"),
        ("enable_web", "false"),
    ],
)
def test_valid_values_also_load_into_settings(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, key: str, value: str
) -> None:
    assert launcher.valid_setting(key, value)
    monkeypatch.setenv(launcher.FIELDS[key][0], value)
    Settings(workspace=isolated)


@pytest.mark.parametrize(
    "key, value",
    [
        ("max_tokens", "10"),
        ("max_tokens", "99999999"),
        ("compact_threshold", "1.5"),
        ("session_budget", "0"),
        ("session_budget", "-1"),
        ("input_price", "free"),
        ("subagent_max_parallel", "0"),
        ("subagent_max_parallel", "33"),
        ("enable_web", "yes"),
        ("residency", "mars"),
        ("not_a_setting", "1"),
        ("aws_secret_access_key", "abc"),
    ],
)
def test_invalid_values_are_refused(isolated: Path, key: str, value: str) -> None:
    assert not launcher.valid_setting(key, value)
    with pytest.raises(typer.BadParameter):
        launcher.save({key: value})
    assert not (isolated / "settings.json").exists()


def test_extreme_valid_values_never_stop_settings_loading(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, (low, high) in launcher.RANGES.items():
        for value in (low, high):
            text = f"{value:.6f}" if isinstance(value, float) else str(value)
            assert launcher.valid_setting(key, text), (key, text)
            monkeypatch.setenv(launcher.FIELDS[key][0], text)
            Settings(workspace=isolated)
            monkeypatch.delenv(launcher.FIELDS[key][0])


def test_damaged_settings_file_entries_are_ignored(isolated: Path) -> None:
    (isolated / "settings.json").write_text(
        json.dumps({"max_tokens": "1", "region": "ap-south-1", "aws_secret": "x", "enable_web": 1})
    )
    assert launcher.load_saved() == {"region": "ap-south-1"}


def test_save_and_clear_keep_other_values(isolated: Path) -> None:
    launcher.save({"region": "ap-south-1", "session_budget": "5"})
    launcher.save({"session_budget": None})
    assert launcher.load_saved() == {"region": "ap-south-1"}


def test_change_applies_now_but_a_users_variable_still_wins(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher.change_settings({"session_budget": "5"})
    assert os.environ["HARNESS_SESSION_BUDGET_USD"] == "5"
    launcher.change_settings({"session_budget": None})
    assert "HARNESS_SESSION_BUDGET_USD" not in os.environ

    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "1000")
    launcher.change_settings({"max_tokens": "2000"})
    assert os.environ["BEDROCK_MAX_TOKENS"] == "1000"
    assert launcher.load_saved()["max_tokens"] == "2000"


def _render(table) -> str:
    from rich.console import Console

    console = Console(width=200, record=True, color_system=None)
    console.print(table)
    return console.export_text()


def test_settings_table_shows_value_and_source(isolated: Path) -> None:
    text = _render(
        settings_table(
            {"session_budget": "5"},
            {"HARNESS_SESSION_BUDGET_USD": "5", "BEDROCK_MAX_TOKENS": "1000"},
        )
    )
    assert "session_budget" in text and "saved" in text
    assert "1000" in text and "environment" in text


def test_command_line_set_unset_and_errors(isolated: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        launcher.launcher, ["--set", "session_budget=2.5", "--set", "enable_web=false"]
    )
    assert result.exit_code == 0, result.output
    assert launcher.load_saved() == {"session_budget": "2.5", "enable_web": "false"}
    assert "session_budget" in result.output

    assert runner.invoke(launcher.launcher, ["--unset", "enable_web"]).exit_code == 0
    assert "enable_web" not in launcher.load_saved()

    for bad in ["session_budget=-1", "nope=1", "session_budget"]:
        assert runner.invoke(launcher.launcher, ["--set", bad]).exit_code == 1
    assert launcher.load_saved() == {"session_budget": "2.5"}

    shown = runner.invoke(launcher.launcher, ["--settings"])
    assert shown.exit_code == 0 and "Settings" in shown.output


def test_refresh_prices_failure_is_clean(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from code_harness import pricing

    def fail(state_dir):
        raise RuntimeError("secret provider text")

    monkeypatch.setattr(pricing, "refresh_prices", fail)
    result = CliRunner().invoke(launcher.launcher, ["--refresh-prices"])
    assert result.exit_code == 1 and "RuntimeError" in result.output
    assert "secret" not in result.output


def _run_page(saved, environ, script):
    async def run():
        app = SettingsApp(saved, environ, [("auto", "auto"), ("m.one", "m.one")])
        async with app.run_test(size=(140, 60)) as pilot:
            await script(app, pilot)
        return app.return_value

    return asyncio.run(run())


def test_page_saves_only_what_changed() -> None:
    async def script(app, pilot):
        app.query_one("#field-session_budget").value = "9.5"
        app.query_one("#field-enable_web").value = "false"
        app.query_one("#field-region").value = ""
        await pilot.pause()
        app.action_save()

    assert _run_page({"region": "ap-south-1", "residency": "india"}, {}, script) == {
        "session_budget": "9.5",
        "enable_web": "false",
        "region": None,
    }


def test_page_refuses_invalid_values_and_stays_open() -> None:
    async def script(app, pilot):
        app.query_one("#field-max_tokens").value = "5"
        await pilot.pause()
        app.action_save()
        await pilot.pause()
        assert app.is_running
        assert "not valid" in str(app.query_one("#message").render())
        app.action_cancel()

    assert _run_page({}, {}, script) is None


def test_page_locks_a_field_set_by_the_environment() -> None:
    async def script(app, pilot):
        field = app.query_one("#field-max_tokens")
        assert field.disabled and field.value == "1000"
        app.action_save()

    assert _run_page({}, {"BEDROCK_MAX_TOKENS": "1000"}, script) == {}


def test_page_escape_cancels_and_buttons_work() -> None:
    async def script(app, pilot):
        await pilot.press("escape")

    assert _run_page({}, {}, script) is None

    async def press_save(app, pilot):
        app.query_one("#save").press()
        await pilot.pause()

    assert _run_page({}, {}, press_save) == {}


def test_model_menu_lists_known_prices_and_skips_no_access(tmp_path: Path) -> None:
    from code_harness import catalog

    catalog.save_cache(
        tmp_path,
        {
            "listing": {"india": ["in.moonshotai.kimi-k3", "in.locked"], "mumbai": ["new.model"]},
            "probes": {"in.locked": "no_access"},
        },
    )
    choices = {value: label for label, value in model_choices(tmp_path, "saved.custom")}
    assert "auto" in choices and "saved.custom" in choices
    assert "$3.3 / $16.5" in choices["in.moonshotai.kimi-k3"]
    assert "price unknown" in choices["new.model"]
    assert "in.locked" not in choices


def _chat(monkeypatch, isolated, edited, script="/settings\n/exit\n"):
    workspace = isolated / "repo"
    workspace.mkdir()
    built: list = []
    monkeypatch.setattr(cli, "_make_agent", lambda *a, **k: built.append(a) or object())
    monkeypatch.setattr(cli, "_edit_settings", lambda: edited)
    monkeypatch.setattr(launcher, "resolve_model", lambda effective: None)
    result = CliRunner().invoke(cli.app, ["chat", "--workspace", str(workspace)], input=script)
    return result, built


def test_chat_settings_rebuilds_the_agent_when_changed(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, built = _chat(monkeypatch, isolated, {"session_budget": "1.5"})
    assert result.exit_code == 0, result.output
    assert "Settings saved and applied" in result.output
    assert len(built) == 2
    assert launcher.load_saved() == {"session_budget": "1.5"}


def test_chat_settings_cancel_changes_nothing(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, built = _chat(monkeypatch, isolated, {})
    assert "No settings changed" in result.output and len(built) == 1


def test_chat_settings_without_a_terminal_shows_the_table(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = isolated / "repo"
    workspace.mkdir()
    monkeypatch.setattr(cli, "_make_agent", lambda *a, **k: object())
    result = CliRunner().invoke(
        cli.app, ["chat", "--workspace", str(workspace)], input="/settings\n/exit\n"
    )
    assert result.exit_code == 0 and "session_budget" in result.output
    assert "--set KEY=VALUE" in result.output
