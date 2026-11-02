"""Load explicitly selected project guidance without executing project code."""

import re
from dataclasses import dataclass

from code_harness.workspace import Workspace, WorkspaceError

MAX_GUIDANCE_CHARS = 32_000
MAX_SKILLS = 5
SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


@dataclass(frozen=True)
class Guidance:
    path: str
    text: str


def load_guidance(
    workspace: Workspace, *, project_instructions: bool, skills: list[str]
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
        paths.append(f".agents/skills/{name}/SKILL.md")
    documents = [Guidance(path, workspace.read(path)) for path in paths]
    if sum(len(document.text) for document in documents) > MAX_GUIDANCE_CHARS:
        raise WorkspaceError("Selected guidance exceeds the 32000-character limit")
    return documents
