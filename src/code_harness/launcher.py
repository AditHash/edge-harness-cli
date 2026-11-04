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
    "residency": ("HARNESS_RESIDENCY", re.compile(r"india|mumbai|any")),
}
RESIDENCY_TIERS = {"india": ("india",), "mumbai": ("mumbai",), "any": ("india", "mumbai")}


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


def print_catalog(models: list, residency: str) -> None:
    from rich.table import Table

    from code_harness.catalog import TIER_LABELS

    allowed = RESIDENCY_TIERS[residency]
    table = Table(title=f"Bedrock models (residency: {residency})")
    for column in ("model id", "tier", "context", "source", "tools"):
        table.add_column(column)
    for item in models:
        if item.tier not in allowed:
            continue
        context = f"{item.context:,}" if item.context else "?"
        table.add_row(item.model_id, item.tier, context, item.context_source, item.tools)
    console.print(table)
    for tier in allowed:
        console.print(f"{tier}: {TIER_LABELS[tier]}", markup=False)
    console.print(
        "context source: sdk = Strands model table; aws-docs = AWS model card; "
        "estimate = provider-published, Bedrock may "
        "serve less. tools: ok/failed from a recorded probe; untested = not checked yet.",
        markup=False,
    )


def catalog_command(residency: str, probes: int) -> None:
    from code_harness import catalog

    models, _ = catalog.load_catalog(state_dir(), refresh=True)
    allowed = RESIDENCY_TIERS[residency]
    if probes:
        pending = [m for m in models if m.tools == "untested" and m.tier in allowed][:probes]
        for item in pending:
            console.print(f"Probing {item.model_id} ...", markup=False)
            catalog.record_probe(state_dir(), item.model_id, catalog.probe_tool_use(item.model_id))
        models, _ = catalog.load_catalog(state_dir())
    print_catalog(models, residency)
    chosen = catalog.choose_auto(models, allowed)
    console.print(
        f"auto would pick: {chosen.model_id if chosen else 'nothing yet (probe some models)'}",
        markup=False,
    )


def resolve_model(effective: dict[str, str]) -> None:
    """Prepare `auto` routing (probing if needed); catalog models are called from Mumbai."""
    from code_harness import catalog

    tiers = RESIDENCY_TIERS[effective.get("residency", "any")]
    if effective.get("model") == "auto":
        chosen = catalog.resolve_auto(
            state_dir(), tiers, notify=lambda text: console.print(text, markup=False)
        )
        route = catalog.routing_candidates(state_dir(), tiers)
        if chosen is None or not route:
            console.print(
                "Auto mode found no tool-capable model for this residency. "
                "Run `edge-harness --probe-models 5` or choose `--model <id>`."
            )
            raise typer.Exit(1)
        os.environ["HARNESS_RESIDENCY"] = effective.get("residency", "any")
        os.environ["AWS_REGION"] = catalog.HOME_REGION
        effective["region"] = catalog.HOME_REGION
        order = ", ".join(
            f"{m.model_id} ({m.context:,})" if m.context else m.model_id for m in route
        )
        console.print(f"Auto routing (per request, with failover): {order}", markup=False)
        return
    model_id = effective.get("model", "")
    mumbai = catalog.load_cache(state_dir()).get("listing", {}).get("mumbai", [])
    # in.* profiles and Mumbai on-demand models must be invoked from ap-south-1.
    needs_mumbai = model_id.startswith("in.") or model_id in mumbai
    if needs_mumbai and effective.get("region") != catalog.HOME_REGION:
        os.environ["AWS_REGION"] = catalog.HOME_REGION
        effective["region"] = catalog.HOME_REGION
        console.print(f"Region set to {catalog.HOME_REGION} for {model_id}.", markup=False)


def mcp_command(add: Path | None, remove: str | None) -> None:
    """Manage saved MCP servers; nothing is started and no AWS call is made."""
    from code_harness.mcp_registry import add_server, load_servers, remove_server
    from code_harness.ui import mcp_table
    from code_harness.workspace import WorkspaceError

    if add is not None:
        try:
            server = add_server(state_dir(), add)
        except WorkspaceError as exc:
            console.print(str(exc), markup=False)
            raise typer.Exit(1) from None
        console.print(
            f"Saved MCP server {server.name}. Connect with --mcp {server.name}", markup=False
        )
    if remove is not None:
        if not remove_server(state_dir(), remove):
            console.print(f"No saved MCP server named {remove}.", markup=False)
            raise typer.Exit(1)
        console.print(f"Removed MCP server {remove}.", markup=False)
    console.print(mcp_table(load_servers(state_dir()), []))


@launcher.command()
def main(
    region: Annotated[str | None, typer.Option(help="Save and use this AWS region")] = None,
    model: Annotated[str | None, typer.Option(help="Save and use this Bedrock model id")] = None,
    profile: Annotated[str | None, typer.Option(help="Save and use this AWS profile")] = None,
    resume: Annotated[
        str | None, typer.Option(help="Resume a session by id; --continue resumes the latest")
    ] = None,
    tui: Annotated[bool, typer.Option("--tui", help="Open the Textual interface")] = False,
    show_config: Annotated[bool, typer.Option("--show-config", help="Print settings")] = False,
    no_login: Annotated[bool, typer.Option("--no-login", help="Skip the credential check")] = False,
    manual: Annotated[bool, typer.Option("--manual", help="Print the command manual")] = False,
    residency: Annotated[
        str | None,
        typer.Option(help="Save: india (in.* only), mumbai (ap-south-1 only) or any"),
    ] = None,
    list_models: Annotated[
        bool, typer.Option("--list-models", help="List India/Mumbai models and exit")
    ] = False,
    probe_models: Annotated[
        int,
        typer.Option(help="Check tool support of N untested models (N small billed calls)"),
    ] = 0,
    continue_last: Annotated[
        bool, typer.Option("--continue", help="Resume the latest session in this folder")
    ] = False,
    skill: Annotated[
        list[str] | None,
        typer.Option("--skill", help="Load .agents/skills/NAME/SKILL.md (repeatable)"),
    ] = None,
    project_instructions: Annotated[
        bool, typer.Option("--project-instructions", help="Load AGENTS.md as guidance")
    ] = False,
    mcp_config: Annotated[
        Path | None, typer.Option(help="Connect the MCP server in this JSON file")
    ] = None,
    mcp: Annotated[
        list[str] | None, typer.Option("--mcp", help="Connect a saved MCP server (repeatable)")
    ] = None,
    mcp_add: Annotated[
        Path | None, typer.Option(help="Save the MCP server in this JSON file, then exit")
    ] = None,
    mcp_remove: Annotated[
        str | None, typer.Option(help="Forget a saved MCP server, then exit")
    ] = None,
    mcp_list: Annotated[bool, typer.Option("--mcp-list", help="List saved MCP servers")] = False,
) -> None:
    """Chat about the current folder. Region/model/profile options are remembered."""
    if manual:
        from code_harness.manual import MANUAL

        console.print(MANUAL, markup=False, highlight=False)
        return
    if mcp_add is not None or mcp_remove is not None or mcp_list:
        mcp_command(mcp_add, mcp_remove)
        return
    updates = {
        key: value
        for key, value in (
            ("region", region),
            ("model", model),
            ("profile", profile),
            ("residency", residency),
        )
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
    if list_models or probe_models:
        if not no_login and not ensure_credentials(
            effective.get("profile"), "ap-south-1", interactive=sys.stdin.isatty()
        ):
            raise typer.Exit(1)
        catalog_command(effective.get("residency", "any"), probe_models)
        return
    if effective.get("model") == "auto":
        effective.setdefault("region", "ap-south-1")
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
    resolve_model(effective)
    from code_harness import cli

    if continue_last and not resume:
        from code_harness.sessions import Sessions
        from code_harness.workspace import WorkspaceError

        try:
            resume = Sessions(state_dir() / "sessions.sqlite", Path.cwd()).latest()
        except WorkspaceError as exc:
            console.print(str(exc), markup=False)
            raise typer.Exit(1) from None
        if resume is None:
            console.print("No earlier session in this folder; starting a new one.")

    if tui:
        cli.main()
        if mcp_config is not None or mcp:
            console.print("MCP servers work only in chat, not with --tui.")
            raise typer.Exit(1)
        cli.tui(
            workspace=Path.cwd(),
            project_instructions=project_instructions,
            skill=skill,
            resume=resume,
        )
    else:
        cli.main()
        cli.chat(
            workspace=Path.cwd(),
            resume=resume,
            project_instructions=project_instructions,
            skill=skill,
            mcp_config=mcp_config,
            mcp=mcp,
        )


def run() -> None:
    launcher()
