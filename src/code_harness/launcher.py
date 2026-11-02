"""`edge-harness`: start chat in the current folder with remembered Bedrock settings.

Saved settings live in ``<HARNESS_STATE_DIR>/settings.json`` (region, model, profile only; never
credentials). Environment variables override saved values. Before chat starts, credentials are
checked with STS ``GetCallerIdentity`` (no model inference, no charge). If they are missing or
expired and the terminal is interactive, the user's own ``aws login`` is run, then re-checked.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

console = Console()
FIELDS = {
    "region": ("AWS_REGION", re.compile(r"[a-z]{2}(-[a-z]+)+-\d{1,2}")),
    "model": ("BEDROCK_MODEL_ID", re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")),
    "profile": ("AWS_PROFILE", re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")),
}


def state_dir() -> Path:
    configured = os.environ.get("HARNESS_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".edge-harness"


def settings_path() -> Path:
    return state_dir() / "settings.json"


def load_saved() -> dict[str, str]:
    """Return only well-formed saved values; anything else is ignored."""
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if key in FIELDS and isinstance(value, str) and FIELDS[key][1].fullmatch(value)
    }


def save(values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in FIELDS or not FIELDS[key][1].fullmatch(value):
            raise typer.BadParameter(f"invalid {key}: {value!r}")
    merged = {**load_saved(), **values}
    directory = state_dir()
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(merged, stream, indent=2, sort_keys=True)
        os.chmod(name, 0o600)
        os.replace(name, settings_path())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def apply_to_environment(saved: dict[str, str]) -> dict[str, str]:
    """Fill unset environment variables from saved values; return the effective settings."""
    effective: dict[str, str] = {}
    for key, (variable, _) in FIELDS.items():
        if not os.environ.get(variable) and key in saved:
            os.environ[variable] = saved[key]
        if os.environ.get(variable):
            effective[key] = os.environ[variable]
    return effective


def credentials_valid(profile: str | None, region: str | None) -> bool:
    """True when the credential chain yields identity; never prints provider error text."""
    try:
        import boto3  # type: ignore[import-untyped]

        session = boto3.Session(profile_name=profile, region_name=region)
        if session.get_credentials() is None:
            return False
        session.client("sts").get_caller_identity()
        return True
    except Exception:  # noqa: BLE001 - any credential/provider failure means "log in"
        return False


def aws_login(profile: str | None, region: str | None) -> bool:
    """Run the user's own `aws login` interactively. Returns False if it cannot run."""
    aws = shutil.which("aws")
    if aws is None:
        console.print("AWS CLI not found on PATH; install it or set credentials another way.")
        return False
    argv = [aws, "login"]
    if profile:
        argv += ["--profile", profile]
    if region:
        argv += ["--region", region]
    console.print("AWS credentials are missing or expired; running `aws login`.")
    try:
        return subprocess.run(argv, check=False).returncode == 0
    except OSError:
        return False


def ensure_credentials(
    profile: str | None,
    region: str | None,
    *,
    interactive: bool,
    check: Callable[[str | None, str | None], bool] = credentials_valid,
    login: Callable[[str | None, str | None], bool] = aws_login,
) -> bool:
    if check(profile, region):
        return True
    if not interactive:
        console.print("AWS credentials are missing or expired. Run `aws login`, then retry.")
        return False
    return login(profile, region) and check(profile, region)


launcher = typer.Typer(add_completion=False)


@launcher.command()
def main(
    region: Annotated[str | None, typer.Option(help="Save and use this AWS region")] = None,
    model: Annotated[str | None, typer.Option(help="Save and use this Bedrock model id")] = None,
    profile: Annotated[str | None, typer.Option(help="Save and use this AWS profile")] = None,
    resume: Annotated[str | None, typer.Option(help="Resume a session id")] = None,
    tui: Annotated[bool, typer.Option("--tui", help="Open the Textual interface")] = False,
    show_config: Annotated[bool, typer.Option("--show-config", help="Print settings")] = False,
    no_login: Annotated[bool, typer.Option("--no-login", help="Skip the credential check")] = False,
) -> None:
    """Chat about the current folder. Region/model/profile options are remembered."""
    updates = {
        key: value
        for key, value in (("region", region), ("model", model), ("profile", profile))
        if value
    }
    if updates:
        save(updates)
    effective = apply_to_environment(load_saved())
    if show_config:
        console.print(f"Settings file: {settings_path()}", markup=False)
        for key in FIELDS:
            console.print(f"{key}: {effective.get(key, 'not set')}", markup=False)
        return
    missing = [key for key in ("region", "model") if key not in effective]
    if missing:
        console.print(
            "Not configured: "
            + ", ".join(missing)
            + ". Run once with e.g. `edge-harness --region ap-south-1 --model <model-id>`."
        )
        raise typer.Exit(1)
    if not no_login and not ensure_credentials(
        effective.get("profile"), effective.get("region"), interactive=sys.stdin.isatty()
    ):
        raise typer.Exit(1)
    from code_harness import cli

    if tui:
        cli.main()
        cli.tui(workspace=Path.cwd(), project_instructions=False, skill=None)
    else:
        cli.main()
        cli.chat(
            workspace=Path.cwd(),
            resume=resume,
            project_instructions=False,
            skill=None,
            mcp_config=None,
        )


def run() -> None:
    launcher()
