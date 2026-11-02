from pathlib import Path
from unittest.mock import MagicMock

from strands.models import Model

from code_harness.agent import create_agent
from code_harness.config import Settings


def test_only_guarded_tools_registered(tmp_path: Path) -> None:
    model = MagicMock(spec=Model)
    model.stateful = False
    model.get_config.return_value = {}
    agent = create_agent(Settings(workspace=tmp_path), model=model)
    assert set(agent.tool_registry.registry) == {"list_repo", "read_code", "search_code"}


def test_explicit_model_and_region_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    import pytest

    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        create_agent(Settings(workspace=tmp_path))
