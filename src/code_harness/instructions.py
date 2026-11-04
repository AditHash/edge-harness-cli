"""Load explicitly selected project guidance without executing project code."""

import re
from dataclasses import dataclass
from pathlib import Path

from code_harness.workspace import Workspace, WorkspaceError

MAX_GUIDANCE_CHARS = 32_000
MAX_SKILLS = 5
SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class Guidance:
    path: str
    text: str


@dataclass(frozen=True)
class SkillInfo:
    name: str
    where: str  # "project" or "global"
    description: str


def _describe(text: str) -> str:
    """The frontmatter description, or else the first non-heading text line."""
    fallback = ""
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if stripped.casefold().startswith("description:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")[:120]
        is_text = stripped and not stripped.startswith(("#", "---"))
        if not fallback and is_text and ":" not in stripped.split(" ", 1)[0]:
            fallback = stripped[:120]
    return fallback


def _skills_in(root: Path) -> list[str]:
    try:
        entries = sorted(path.name for path in root.iterdir() if path.is_dir())
    except OSError:
        return []
    return [name for name in entries if SKILL_NAME.fullmatch(name)]


def list_skills(workspace: Workspace, global_dir: Path | None = None) -> list[SkillInfo]:
    """Skills available by name; a project skill hides a global one of the same name."""
    found: dict[str, SkillInfo] = {}
    for name in _skills_in(workspace.root / ".agents" / "skills"):
        try:
            text = workspace.read(f".agents/skills/{name}/SKILL.md")
        except WorkspaceError:
            continue
        found[name] = SkillInfo(name, "project", _describe(text))
    if global_dir is not None and global_dir.is_dir():
        shared = Workspace(global_dir)
        for name in _skills_in(global_dir):
            if name in found:
                continue
            try:
                text = shared.read(f"{name}/SKILL.md")
            except WorkspaceError:
                continue
            found[name] = SkillInfo(name, "global", _describe(text))
    return [found[name] for name in sorted(found)]


def _read_skill(workspace: Workspace, name: str, global_dir: Path | None) -> Guidance:
    project_path = f".agents/skills/{name}/SKILL.md"
    if (workspace.root / project_path).exists() or global_dir is None:
        return Guidance(project_path, workspace.read(project_path))
    if not (global_dir / name / "SKILL.md").exists():
        raise WorkspaceError(f"Skill {name} was not found")
    return Guidance(f"global:{name}/SKILL.md", Workspace(global_dir).read(f"{name}/SKILL.md"))


def load_guidance(
    workspace: Workspace,
    *,
    project_instructions: bool,
    skills: list[str],
    global_dir: Path | None = None,
) -> list[Guidance]:
    """Read only user-selected instructions and skill files, under workspace policy."""
    if len(skills) > MAX_SKILLS or len(set(skills)) != len(skills):
        raise WorkspaceError("Select at most five distinct skills")
    paths = ["AGENTS.md"] if project_instructions else []
    for name in skills:
        if not SKILL_NAME.fullmatch(name):
            raise WorkspaceError(
                "Skill names must contain only letters, numbers, underscores or hyphens"
            )
    documents = [Guidance(path, workspace.read(path)) for path in paths]
    documents += [_read_skill(workspace, name, global_dir) for name in skills]
    if sum(len(document.text) for document in documents) > MAX_GUIDANCE_CHARS:
        raise WorkspaceError("Selected guidance exceeds the 32000-character limit")
    return documents
