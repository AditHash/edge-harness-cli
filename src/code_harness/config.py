from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model_id: str | None = None
    bedrock_max_tokens: int = Field(default=16384, ge=256, le=65536)
    harness_compact_threshold: float = Field(default=0.7, gt=0.1, le=0.95)
    harness_residency: str = Field(default="any", pattern=r"^(india|mumbai|any)$")
    sandbox_wsl_distribution: str = "Ubuntu"
    harness_state_dir: Path = Field(default_factory=lambda: Path.home() / ".edge-harness")
    harness_enable_explorer: bool = False
    harness_enable_reviewer: bool = False
    harness_enable_tester: bool = False
    harness_enable_subagents: bool = True
    harness_enable_web: bool = True
    harness_enable_browser: bool = True
    harness_browser_local_ports: str = ""
    harness_browser_headed: bool = False
    harness_enable_host_commands: bool = True
    harness_subagent_max_parallel: int = Field(default=8, ge=1, le=32)
    harness_subagent_turn_tokens: int = Field(default=2_000_000, ge=0)
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    cached_price_per_mtok: float | None = None
    harness_session_budget_usd: float | None = Field(default=None, gt=0)
    workspace: Path = Field(default_factory=Path.cwd)

    @field_validator("workspace")
    @classmethod
    def valid_workspace(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        return path
