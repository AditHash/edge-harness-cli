import asyncio
import os
import sys
from pathlib import Path

import typer
from rich.console import Console

from code_harness.config import Settings
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
    console.print()


def _make_agent(workspace: Path) -> object:
    from code_harness.agent import create_agent

    return create_agent(Settings(workspace=workspace))


@app.command()
def ask(prompt: str, workspace: Path | None = None) -> None:
    """Ask a read-only question about a workspace."""
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
def chat(workspace: Path | None = None) -> None:
    """Start a read-only interactive conversation."""
    workspace = workspace or Path.cwd()
    try:
        agent = _make_agent(workspace)
        console.print("Read-only session. Type /help for commands.")
        while True:
            try:
                prompt = input("You > ").strip()
            except EOFError:
                break
            if prompt in {"/exit", "/quit"}:
                break
            if prompt == "/help":
                console.print("/help  /new  /status  /exit")
            elif prompt == "/new":
                agent = _make_agent(workspace)
                console.print("Started a new in-memory conversation.")
            elif prompt == "/status":
                console.print(f"Workspace: {workspace.resolve()} (read-only, in-memory)")
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
