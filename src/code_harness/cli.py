import os
import sys

import typer
from rich.console import Console

from code_harness.config import Settings

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
