from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from code_harness.cli import app
from code_harness.config import Settings


def test_help_and_doctor_do_not_need_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    runner = CliRunner()
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "No AWS calls" in result.output


def test_workspace_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(workspace=tmp_path / "missing")


def test_doctor_never_prints_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-sensitive-value")
    result = CliRunner().invoke(app, ["doctor"])
    assert "test-sensitive-value" not in result.output
