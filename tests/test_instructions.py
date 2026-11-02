from pathlib import Path

import pytest

from code_harness.instructions import load_guidance
from code_harness.workspace import Workspace, WorkspaceError


def test_guidance_requires_explicit_selection_and_stays_in_workspace(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Use project conventions")
    skill = tmp_path / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("Review for missing tests")
    workspace = Workspace(tmp_path)
    assert load_guidance(workspace, project_instructions=False, skills=[]) == []
    selected = load_guidance(workspace, project_instructions=True, skills=["review"])
    assert [item.path for item in selected] == ["AGENTS.md", ".agents/skills/review/SKILL.md"]
    with pytest.raises(WorkspaceError):
        load_guidance(workspace, project_instructions=False, skills=["../../outside"])


def test_guidance_never_follows_link_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("do not read")
    try:
        (root / "AGENTS.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(WorkspaceError):
        load_guidance(Workspace(root), project_instructions=True, skills=[])
