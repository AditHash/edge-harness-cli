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


def test_skill_discovery_and_global_fallback(tmp_path: Path) -> None:
    from code_harness.instructions import list_skills

    root = tmp_path / "repo"
    (root / ".agents" / "skills" / "local").mkdir(parents=True)
    (root / ".agents" / "skills" / "local" / "SKILL.md").write_text(
        "---\nname: local\ndescription: Project skill\n---\nBody"
    )
    (root / ".agents" / "skills" / "both").mkdir()
    (root / ".agents" / "skills" / "both" / "SKILL.md").write_text("Project wins")
    (root / ".agents" / "skills" / "empty").mkdir()
    shared = tmp_path / "state" / "skills"
    (shared / "shared").mkdir(parents=True)
    (shared / "shared" / "SKILL.md").write_text("# Title\nShared helper")
    (shared / "both").mkdir()
    (shared / "both" / "SKILL.md").write_text("Global loses")
    workspace = Workspace(root)
    found = {info.name: info for info in list_skills(workspace, shared)}
    assert set(found) == {"local", "both", "shared"}
    assert found["local"].description == "Project skill"
    assert found["both"].where == "project" and found["both"].description == "Project wins"
    assert found["shared"].where == "global" and found["shared"].description == "Shared helper"

    documents = load_guidance(
        workspace, project_instructions=False, skills=["shared", "both"], global_dir=shared
    )
    assert [document.path for document in documents] == [
        "global:shared/SKILL.md",
        ".agents/skills/both/SKILL.md",
    ]
    assert documents[1].text == "Project wins"
    with pytest.raises(WorkspaceError):
        load_guidance(workspace, project_instructions=False, skills=["nope"], global_dir=shared)


def test_global_skill_cannot_escape_through_links_or_names(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    shared = tmp_path / "skills"
    shared.mkdir()
    with pytest.raises(WorkspaceError):
        load_guidance(
            Workspace(root), project_instructions=False, skills=["../x"], global_dir=shared
        )


def test_chat_lists_and_loads_skills_and_refuses_unknown(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from code_harness import cli

    root = tmp_path / "repo"
    skill = root / ".agents" / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("description: Review code")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    built: list[list[str]] = []

    def make_agent(workspace, session_manager=None, guidance=None, model_id=None, mcp_tools=None):
        built.append([item.path for item in guidance or []])
        return object()

    monkeypatch.setattr(cli, "_make_agent", make_agent)
    result = CliRunner().invoke(
        cli.app,
        ["chat", "--workspace", str(root)],
        input="/skills\n/skill nope\n/skill review\n/skills\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "Review code" in result.output and "Loaded skill review" in result.output
    assert "Skill nope was not found" in result.output or "not found" in result.output
    assert built == [[], [".agents/skills/review/SKILL.md"]]
    assert "loaded" in result.output
