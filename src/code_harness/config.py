from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model_id: str | None = None
    sandbox_wsl_distribution: str = "Ubuntu"
    harness_state_dir: Path = Field(default_factory=lambda: Path.home() / ".edge-harness")
    harness_enable_explorer: bool = False
    harness_enable_reviewer: bool = False
    harness_enable_tester: bool = False
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    workspace: Path = Field(default_factory=Path.cwd)

    @field_validator("workspace")
    @classmethod
    def valid_workspace(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        return path
