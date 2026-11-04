import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape

from code_harness.approvals import (
    SessionApprovals,
    review_command,
    review_mcp,
    review_proposal,
)
from code_harness.config import Settings
from code_harness.instructions import Guidance, load_guidance
from code_harness.proposals import Proposal
from code_harness.sessions import Sessions
from code_harness.streaming import stream_turn
from code_harness.ui import CODE_THEME, THEME, changes_table
from code_harness.usage import UsageTracker
from code_harness.workspace import Workspace, WorkspaceError

app = typer.Typer(no_args_is_help=True, help="A local-first coding assistant.")
console = Console(theme=THEME)
# Standing approvals granted with `all`; reset when a chat starts.
SESSION_APPROVALS = SessionApprovals()
# (path, added, removed, kind) for every file change approved in this chat.
SESSION_CHANGES: list[tuple[str, int, int, str]] = []


def _model_report(agent: Any, configured: str | None) -> str:
    """Configured model; in auto mode also the route and which model is serving now."""
    if not configured:
        return "Model: not configured"
    model = getattr(agent, "model", None)
    candidates = getattr(model, "candidates", None)
    if configured != "auto" or not candidates:
        return f"Model: {configured}"
    dropped: set[str] = getattr(model, "dropped", set())
    lines = [f"Model: auto (now serving {getattr(model, 'active_id', '?')})", "Route:"]
    for model_id, _, context in candidates:
        size = f"{context:,}" if context else "unknown"
        state = " - skipped (no access)" if model_id in dropped else ""
        lines.append(f"  {model_id} (context {size}){state}")
    return "\n".join(lines)


def _context_report(agent: Any, settings: Settings) -> str:
    """Current context use against the model's window and the auto-compact threshold."""
    model = getattr(agent, "model", None)
    limit = getattr(model, "context_window_limit", None)
    metrics = getattr(agent, "event_loop_metrics", None)
    used = getattr(metrics, "latest_context_size", None)
    threshold = settings.harness_compact_threshold
    if not isinstance(limit, int) or not limit:
        return "Context window unknown for this model; auto-compact uses the SDK default."
    if not isinstance(used, int):
        return (
            f"Context window {limit:,} tokens; no model call yet. Auto-compact at {threshold:.0%}."
        )
    return (
        f"Context: {used:,} / {limit:,} tokens ({used / limit:.1%}). "
        f"Auto-compact at {threshold:.0%} ({int(limit * threshold):,} tokens)."
    )


def _failure_message(exc: BaseException) -> str:
    """Actionable text without provider error contents."""
    name = type(exc).__name__
    if name == "MaxTokensReachedException":
        return (
            "Turn stopped: the model hit its output limit (BEDROCK_MAX_TOKENS). Nothing was "
            "changed by the unfinished step. Ask for a smaller change, or raise the limit."
        )
    if name in {"ExpiredTokenException", "NoCredentialsError", "UnauthorizedSSOTokenError"}:
        return "Turn failed: AWS credentials are missing or expired. Run `aws login`."
    return f"Turn failed ({name})."


@app.callback()
def main() -> None:
    """Inspect and work with a repository using an AI assistant."""
    import logging

    # SDK warnings can echo raw tool input; keep the terminal to our own messages.
    logging.getLogger("strands").setLevel(logging.ERROR)
    # Model output may contain characters the legacy Windows code page cannot encode.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


@app.command()
def doctor() -> None:
    """Check local configuration without making cloud calls."""
    try:
        settings = Settings()
    except ValueError:
        console.print("[red]Invalid configuration: check workspace and environment settings.[/red]")
        raise typer.Exit(1) from None
    console.print(f"Python: {sys.version_info.major}.{sys.version_info.minor}")
    console.print(f"Workspace: {settings.workspace}")
    console.print(f"AWS region configured: {'yes' if settings.aws_region else 'no'}")
    console.print(f"Bedrock model configured: {'yes' if settings.bedrock_model_id else 'no'}")
    console.print(f"AWS profile configured: {'yes' if settings.aws_profile else 'no'}")
    console.print(
        f"AWS environment credentials present: {'yes' if os.getenv('AWS_ACCESS_KEY_ID') else 'no'}"
    )
    from code_harness.platforms import sandbox_backend, sandbox_unavailable_reason

    backend = sandbox_backend()
    if backend:
        console.print(f"Command sandbox backend: {backend}")
    else:
        console.print(f"Command sandbox: disabled ({sandbox_unavailable_reason()})")
    console.print(f"Git on PATH: {'yes' if shutil.which('git') else 'no'}")
    console.print(
        f"File edits: {'enabled' if os.name == 'nt' or hasattr(os, 'O_DIRECTORY') else 'disabled'}"
    )
    console.print("No AWS calls were made. Credentials may also be supplied by the provider chain.")


class _ThinkingMeter:
    """Streams the model's reasoning (dimmed) and counts it (~4 characters per token).

    With ``show`` off, a spinner with the running count replaces the text.
    """

    def __init__(self, show: bool = True) -> None:
        self.show = show
        self.chars = 0
        self.active = False
        self.status: Any = None

    def add(self, text: str) -> None:
        self.chars += len(text)
        if self.show:
            if not self.active:
                console.print("\n[dim]Thinking:[/dim]")
            console.print(text, end="", style="dim italic", markup=False, highlight=False)
        else:
            message = f"Thinking... ~{self.chars // 4:,} tokens"
            if self.status is None:
                self.status = console.status(message, spinner="dots")
                self.status.start()
            else:
                self.status.update(message)
        self.active = True

    def stop(self) -> None:
        if not self.active:
            return
        if self.status is not None:
            self.status.stop()
            self.status = None
        console.print(f"\n[dim](thinking: ~{self.chars // 4:,} tokens)[/dim]")
        self.active = False
        self.chars = 0


def _token_text(event: Any) -> str:
    cached = f" + {event.cached_tokens:,} cached" if event.cached_tokens else ""
    return f"{event.input_tokens:,} in{cached} / {event.output_tokens:,} out"


class _ReplyView:
    """Streams the assistant's reply as live-rendered Markdown, one block per text segment."""

    def __init__(self) -> None:
        self.text = ""
        self.live: Any = None

    def add(self, delta: str) -> None:
        from rich.live import Live
        from rich.markdown import Markdown

        self.text += delta
        if self.live is None:
            self.live = Live(
                Markdown(self.text, code_theme=CODE_THEME),
                console=console,
                refresh_per_second=8,
            )
            self.live.start()
        else:
            self.live.update(Markdown(self.text, code_theme=CODE_THEME))

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
            if not console.is_terminal:
                # Live leaves the cursor on the last line when output is not a terminal.
                console.print()
        self.text = ""


async def _show_turn(agent: object, prompt: str, tracker: UsageTracker | None = None) -> None:
    meter = _ThinkingMeter(show=os.environ.get("HARNESS_SHOW_THINKING", "true").lower() != "false")
    reply = _ReplyView()
    changes_before = len(SESSION_CHANGES)
    try:
        async for event in stream_turn(agent, prompt):
            if event.kind == "reasoning":
                reply.stop()
                meter.add(event.text)
                continue
            meter.stop()
            if event.kind == "text_delta":
                reply.add(event.text)
                continue
            reply.stop()
            if event.kind == "tool_call":
                console.print(f"[cyan]  > {escape(event.text)}[/cyan]", highlight=False)
            elif event.kind == "tool_result":
                if event.text.endswith(": failed"):
                    console.print(f"[red]  x {escape(event.text)}[/red]", highlight=False)
            elif event.kind == "call_usage":
                served = f"{escape(event.text)}  " if event.text else ""
                console.print(
                    f"[dim]    {served}{_token_text(event)}[/dim]",
                    highlight=False,
                )
            elif event.kind == "usage":
                if tracker is not None:
                    tracker.add(event.input_tokens, event.output_tokens, event.cached_tokens)
                console.rule(
                    f"[dim]turn: {_token_text(event)}[/dim]",
                    style="dim",
                )
            elif event.kind == "compacted":
                console.print(f"[cyan]{escape(event.text)}[/cyan]")
            elif event.kind == "error":
                console.print(f"[yellow]{escape(event.text)}[/yellow]")
    finally:
        reply.stop()
        meter.stop()
    if len(SESSION_CHANGES) > changes_before:
        console.print(changes_table(SESSION_CHANGES[changes_before:], "Changed this turn"))


@dataclass(frozen=True)
class ReviewIO:
    """How approval prompts reach the user: terminal by default, a TUI modal otherwise."""

    interactive: bool
    display: Callable[[str], None]
    read_reply: Callable[[str], str]


def _terminal_io() -> ReviewIO:
    from code_harness.ui import review_renderable

    def ask(prompt: str) -> str:
        # The prompt text is ours, never model output.
        return console.input(f"[bold yellow]? {escape(prompt)}[/bold yellow]")

    return ReviewIO(
        sys.stdin.isatty(),
        lambda text: console.print(review_renderable(text)),
        ask,
    )


def _make_agent(
    workspace: Path,
    session_manager=None,
    guidance: list[Guidance] | None = None,
    model_id: str | None = None,
    mcp_tools: list[Any] | None = None,
    review_io: ReviewIO | None = None,
) -> Any:
    from code_harness.agent import create_agent

    def io() -> ReviewIO:
        return review_io or _terminal_io()

    def review(proposal: Proposal) -> bool:
        ui = io()
        approved = review_proposal(
            proposal,
            interactive=ui.interactive,
            display=ui.display,
            read_reply=ui.read_reply,
            session=SESSION_APPROVALS,
        )
        if approved:
            from code_harness.ui import diff_stats

            added, removed = diff_stats(proposal.diff)
            kind = (
                "deleted"
                if proposal.new_content is None
                else "created"
                if proposal.original_digest is None
                else "edited"
            )
            SESSION_CHANGES.append((proposal.path, added, removed, kind))
        return approved

    def command_review(command: str) -> bool:
        ui = io()
        return review_command(
            command,
            interactive=ui.interactive,
            display=ui.display,
            read_reply=ui.read_reply,
            session=SESSION_APPROVALS,
        )

    def mcp_review(name: str, arguments: str) -> bool:
        ui = io()
        return review_mcp(
            f"tool call {name}",
            arguments,
            interactive=ui.interactive,
            display=ui.display,
            read_reply=ui.read_reply,
        )

    settings = Settings(workspace=workspace)
    if model_id is not None:
        settings = settings.model_copy(update={"bedrock_model_id": model_id})
    return create_agent(
        settings,
        review=review,
        review_command=command_review,
        session_manager=session_manager,
        guidance=guidance,
        mcp_tools=mcp_tools,
        mcp_review=mcp_review if mcp_tools else None,
        notify=lambda text: console.print(f"[cyan]{text}[/cyan]", highlight=False),
    )


def _open_mcp(
    stack: contextlib.ExitStack, config_path: Path | None, settings: Settings
) -> list[Any]:
    if config_path is None:
        return []
    from code_harness.mcp_integration import connect_sandboxed_mcp, load_mcp_config

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise WorkspaceError("Invalid MCP configuration file") from None
    if isinstance(raw, dict) and "url" in raw:
        return _open_remote_mcp(stack, config_path)
    config = load_mcp_config(config_path)
    details = json.dumps(
        {"name": config.name, "command": config.command}, ensure_ascii=True, indent=2
    )
    if not review_mcp(
        "server startup",
        details,
        interactive=sys.stdin.isatty(),
        display=lambda text: console.print(text, markup=False, highlight=False),
        read_reply=input,
    ):
        raise WorkspaceError("MCP server startup was not approved")
    tools, omitted = stack.enter_context(
        connect_sandboxed_mcp(
            Workspace(settings.workspace),
            config,
            settings.harness_state_dir / "mcp-temp",
            distro=settings.sandbox_wsl_distribution,
        )
    )
    console.print(
        f"MCP server {config.name}: {len(tools)} tools; {omitted} snapshot files omitted."
    )
    return tools


def _open_remote_mcp(stack: contextlib.ExitStack, config_path: Path) -> list[Any]:
    from code_harness.mcp_remote import connect_remote_mcp, load_remote_config, validate_remote_url

    config = load_remote_config(config_path)
    validate_remote_url(config.url)
    details = json.dumps({"name": config.name, "url": config.url}, ensure_ascii=True, indent=2)
    if not review_mcp(
        "REMOTE server connection",
        details,
        interactive=sys.stdin.isatty(),
        note="Approved tool arguments are sent to this remote host. No credentials are sent.",
        display=lambda text: console.print(text, markup=False, highlight=False),
        read_reply=input,
    ):
        raise WorkspaceError("Remote MCP connection was not approved")
    tools, _ = stack.enter_context(connect_remote_mcp(config))
    console.print(f"Remote MCP server {config.name}: {len(tools)} tools.")
    return tools


@app.command()
def ask(
    prompt: str,
    workspace: Path | None = None,
    project_instructions: bool = False,
    skill: Annotated[list[str] | None, typer.Option("--skill")] = None,
    mcp_config: Path | None = None,
) -> None:
    """Ask a question about a workspace; edits require approval."""
    try:
        root = workspace or Path.cwd()
        guidance = load_guidance(
            Workspace(root), project_instructions=project_instructions, skills=skill or []
        )
        with contextlib.ExitStack() as stack:
            mcp_tools = _open_mcp(stack, mcp_config, Settings(workspace=root))
            asyncio.run(
                _show_turn(_make_agent(root, guidance=guidance, mcp_tools=mcp_tools), prompt)
            )
    except KeyboardInterrupt:
        console.print("\nCancelled.")
        raise typer.Exit(130) from None
    except WorkspaceError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001 - keep provider error contents out of the terminal
        console.print(
            f"[red]Agent failed ({type(exc).__name__}). Check model, region and access.[/red]"
        )
        raise typer.Exit(1) from None


@app.command()
def chat(
    workspace: Path | None = None,
    resume: str | None = None,
    project_instructions: bool = False,
    skill: Annotated[list[str] | None, typer.Option("--skill")] = None,
    mcp_config: Path | None = None,
) -> None:
    """Start an interactive conversation; edits require approval."""
    workspace = workspace or Path.cwd()
    stack = contextlib.ExitStack()
    try:
        settings = Settings(workspace=workspace)
        current_model = settings.bedrock_model_id
        guidance = load_guidance(
            Workspace(settings.workspace),
            project_instructions=project_instructions,
            skills=skill or [],
        )
        mcp_tools = _open_mcp(stack, mcp_config, settings)
        sessions = Sessions(settings.harness_state_dir / "sessions.sqlite", settings.workspace)
        tracker = UsageTracker(settings.input_price_per_mtok, settings.output_price_per_mtok)
        SESSION_APPROVALS.reset()
        SESSION_CHANGES.clear()
        session_id = resume or sessions.create()
        manager = sessions.manager(session_id)
        agent = _make_agent(workspace, manager, guidance, current_model, mcp_tools)
        from code_harness.ui import session_header

        console.print(
            session_header(
                workspace=str(Path(workspace).resolve()),
                model=current_model or "not configured",
                session_id=session_id,
                guidance=[item.path for item in guidance],
            )
        )
        while True:
            try:
                prompt = console.input("[bold green]you >[/bold green] ").strip()
            except EOFError:
                break
            if prompt in {"/exit", "/quit"}:
                break
            if prompt == "/help":
                console.print(
                    "/help  /new  /clear  /sessions  /resume ID  /compact  /model [ID]  /status  /usage  /context  /plan  /changes  /approve [ask]  /exit"
                )
            elif prompt in {"/new", "/clear"}:
                session_id = sessions.create()
                manager = sessions.manager(session_id)
                agent = _make_agent(workspace, manager, guidance, current_model, mcp_tools)
                console.print(f"Started session {session_id}.")
            elif prompt == "/sessions":
                console.print("\n".join(sessions.list()) or "No sessions for this workspace.")
            elif prompt == "/resume":
                console.print("Use /sessions to list IDs, then /resume ID.")
            elif prompt.startswith("/resume "):
                candidate = prompt.removeprefix("/resume ").strip()
                try:
                    candidate_manager = sessions.manager(candidate)
                    replacement = _make_agent(
                        workspace, candidate_manager, guidance, current_model, mcp_tools
                    )
                except Exception as exc:  # noqa: BLE001 - do not expose persisted content
                    console.print(f"[red]Cannot resume session ({type(exc).__name__}).[/red]")
                else:
                    agent = replacement
                    manager = candidate_manager
                    session_id = candidate
                    console.print(f"Resumed session {session_id}.")
            elif prompt == "/compact":
                try:
                    before = len(agent.messages)
                    agent.conversation_manager.reduce_context(agent)
                    removed = before - len(agent.messages)
                    if removed:
                        asyncio.run(manager.save_snapshot(agent, is_latest=True))
                    console.print(
                        f"Compacted {removed} older messages into a model-written summary."
                    )
                except Exception as exc:  # noqa: BLE001 - do not expose persisted content
                    console.print(f"[red]Could not compact session ({type(exc).__name__}).[/red]")
            elif prompt == "/model":
                console.print(_model_report(agent, current_model), markup=False)
            elif prompt.startswith("/model "):
                candidate = prompt.removeprefix("/model ").strip()
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", candidate):
                    console.print("[red]Invalid Bedrock model or inference profile ID.[/red]")
                    continue
                try:
                    candidate_manager = sessions.manager(session_id)
                    replacement = _make_agent(
                        workspace, candidate_manager, guidance, candidate, mcp_tools
                    )
                except Exception as exc:  # noqa: BLE001 - provider errors may contain sensitive data
                    console.print(f"[red]Cannot switch model ({type(exc).__name__}).[/red]")
                else:
                    agent = replacement
                    manager = candidate_manager
                    current_model = candidate
                    console.print(
                        f"Model changed to {candidate}. Access is checked on the next request."
                    )
            elif prompt == "/plan":
                from code_harness.planning import render_plan

                saved = agent.state.get("plan") if hasattr(agent, "state") else None
                console.print(render_plan(saved) if saved else "No plan recorded.", markup=False)
            elif prompt == "/changes":
                if SESSION_CHANGES:
                    console.print(changes_table(SESSION_CHANGES, "Changed this session"))
                else:
                    console.print("No approved file changes in this session.")
            elif prompt == "/approve":
                console.print(SESSION_APPROVALS.describe(), markup=False)
            elif prompt == "/approve ask":
                SESSION_APPROVALS.reset()
                console.print("Standing approvals cleared. " + SESSION_APPROVALS.describe())
            elif prompt == "/usage":
                console.print(tracker.summary(), markup=False)
            elif prompt == "/context":
                console.print(_context_report(agent, settings), markup=False)
            elif prompt == "/status":
                console.print(f"Workspace: {workspace.resolve()}; session: {session_id}")
            elif prompt.startswith("/"):
                console.print("Unknown command. Type /help to see available commands.")
            elif prompt:
                try:
                    asyncio.run(_show_turn(agent, prompt, tracker))
                except KeyboardInterrupt:
                    console.print("\nTurn cancelled.")
                except Exception as exc:  # noqa: BLE001 - provider errors may contain sensitive data
                    console.print(f"[red]{_failure_message(exc)}[/red]", markup=True)
    except KeyboardInterrupt:
        console.print("\nSession ended.")
    except WorkspaceError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001 - do not expose provider error contents
        console.print(f"[red]Cannot start agent ({type(exc).__name__}); check configuration.[/red]")
        raise typer.Exit(1) from None
    finally:
        stack.close()


@app.command()
def tui(
    workspace: Path | None = None,
    project_instructions: bool = False,
    skill: Annotated[list[str] | None, typer.Option("--skill")] = None,
) -> None:
    """Open the Textual interface (approvals appear as modal dialogs)."""
    from code_harness.tui import HarnessApp

    root = (workspace or Path.cwd()).resolve()
    try:
        settings = Settings(workspace=root)
        guidance = load_guidance(
            Workspace(root), project_instructions=project_instructions, skills=skill or []
        )
        sessions = Sessions(settings.harness_state_dir / "sessions.sqlite", settings.workspace)
        manager = sessions.manager(sessions.create())
        tracker = UsageTracker(settings.input_price_per_mtok, settings.output_price_per_mtok)

        def make_agent(display: Callable[[str], None], read_reply: Callable[[str], str]) -> Any:
            return _make_agent(
                root, manager, guidance, review_io=ReviewIO(True, display, read_reply)
            )

        HarnessApp(make_agent, tracker).run()
    except WorkspaceError as exc:
        console.print(str(exc), markup=False)
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001 - do not expose provider error contents
        console.print(f"[red]Cannot start TUI ({type(exc).__name__}); check configuration.[/red]")
        raise typer.Exit(1) from None
