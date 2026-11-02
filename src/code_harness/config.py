from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model_id: str | None = None
    workspace: Path = Field(default_factory=Path.cwd)

    @field_validator("workspace")
    @classmethod
    def valid_workspace(cls, value: Path) -> Path:
        path = value.expanduser().resolve()
        if not path.is_dir():
            raise ValueError("workspace must be an existing directory")
        return path
