import asyncio
import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from code_harness.approvals import review_command, review_proposal
from code_harness.config import Settings
from code_harness.proposals import Proposal
from code_harness.sessions import Sessions
from code_harness.streaming import stream_turn

app = typer.Typer(no_args_is_help=True, help="A local-first coding assistant.")
console = Console()


@app.callback()
def main() -> None:
    """Inspect and work with a repository using an AI assistant."""


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
    console.print("No AWS calls were made. Credentials may also be supplied by the provider chain.")


async def _show_turn(agent: object, prompt: str) -> None:
    async for event in stream_turn(agent, prompt):
        if event.kind == "text_delta":
            console.print(event.text, end="", markup=False, highlight=False, soft_wrap=True)
        elif event.kind == "tool_started":
            console.print(f"\n[dim]Tool: {event.text}[/dim]")
        elif event.kind == "tool_result":
            console.print(f"[dim]{event.text}[/dim]")
        elif event.kind == "usage":
            console.print(f"\n[dim]{event.text}[/dim]")
        elif event.kind == "error":
            console.print(f"\n[yellow]{event.text}[/yellow]")
    console.print()


def _make_agent(workspace: Path, session_manager=None) -> object:
    from code_harness.agent import create_agent

    def review(proposal: Proposal) -> bool:
        return review_proposal(
            proposal,
            interactive=sys.stdin.isatty(),
            display=lambda text: console.print(text, markup=False, highlight=False),
            read_reply=input,
        )

    def command_review(command: str) -> bool:
        return review_command(
            command,
            interactive=sys.stdin.isatty(),
            display=lambda text: console.print(text, markup=False, highlight=False),
            read_reply=input,
        )

    return create_agent(
        Settings(workspace=workspace),
        review=review,
        review_command=command_review,
        session_manager=session_manager,
    )


@app.command()
def ask(prompt: str, workspace: Path | None = None) -> None:
    """Ask a question about a workspace; edits require approval."""
    try:
        asyncio.run(_show_turn(_make_agent(workspace or Path.cwd()), prompt))
    except KeyboardInterrupt:
        console.print("\nCancelled.")
        raise typer.Exit(130) from None
    except Exception as exc:  # noqa: BLE001 - keep provider error contents out of the terminal
        console.print(
            f"[red]Agent failed ({type(exc).__name__}). Check model, region and access.[/red]"
        )
        raise typer.Exit(1) from None


@app.command()
def chat(workspace: Path | None = None, resume: str | None = None) -> None:
    """Start an interactive conversation; edits require approval."""
    workspace = workspace or Path.cwd()
    try:
        settings = Settings(workspace=workspace)
        sessions = Sessions(settings.harness_state_dir / "sessions.sqlite", settings.workspace)
        session_id = resume or sessions.create()
        agent = _make_agent(workspace, sessions.manager(session_id))
        console.print(f"Session: {session_id}. Conversations are stored locally.")
        console.print(
            "File changes and sandbox commands require approval. Type /help for commands."
        )
        while True:
            try:
                prompt = input("You > ").strip()
            except EOFError:
                break
            if prompt in {"/exit", "/quit"}:
                break
            if prompt == "/help":
                console.print("/help  /new  /clear  /sessions  /resume ID  /model  /status  /exit")
            elif prompt in {"/new", "/clear"}:
                session_id = sessions.create()
                agent = _make_agent(workspace, sessions.manager(session_id))
                console.print(f"Started session {session_id}.")
            elif prompt == "/sessions":
                console.print("\n".join(sessions.list()) or "No sessions for this workspace.")
            elif prompt == "/resume":
                console.print("Use /sessions to list IDs, then /resume ID.")
            elif prompt.startswith("/resume "):
                candidate = prompt.removeprefix("/resume ").strip()
                try:
                    manager = sessions.manager(candidate)
                    replacement = _make_agent(workspace, manager)
                except Exception as exc:  # noqa: BLE001 - do not expose persisted content
                    console.print(f"[red]Cannot resume session ({type(exc).__name__}).[/red]")
                else:
                    agent = replacement
                    session_id = candidate
                    console.print(f"Resumed session {session_id}.")
            elif prompt == "/model":
                console.print(f"Model: {settings.bedrock_model_id or 'not configured'}")
            elif prompt == "/status":
                console.print(f"Workspace: {workspace.resolve()}; session: {session_id}")
            elif prompt.startswith("/"):
                console.print("Unknown command. Type /help to see available commands.")
            elif prompt:
                try:
                    asyncio.run(_show_turn(agent, prompt))
                except KeyboardInterrupt:
                    console.print("\nTurn cancelled.")
                except Exception as exc:  # noqa: BLE001 - provider errors may contain sensitive data
                    console.print(f"[red]Turn failed ({type(exc).__name__}).[/red]")
    except KeyboardInterrupt:
        console.print("\nSession ended.")
    except Exception as exc:  # noqa: BLE001 - do not expose provider error contents
        console.print(f"[red]Cannot start agent ({type(exc).__name__}); check configuration.[/red]")
        raise typer.Exit(1) from None
