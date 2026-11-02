from pathlib import Path

import pytest

from code_harness.retrieval import WorkspaceRetriever, tokenize
from code_harness.workspace import Workspace, WorkspaceError

SRC = Path(__file__).parent.parent / "src" / "code_harness"

# (natural-language question, file that actually answers it)
BENCHMARK = [
    ("how are conversation sessions saved to sqlite", "sessions.py"),
    ("where does the sandbox limit memory and processes", "wsl_sandbox_helper.py"),
    ("reject symlink or junction links outside the workspace", "workspace.py"),
    ("validate remote mcp url private address", "mcp_remote.py"),
    ("hook that cancels an mcp tool call without approval", "mcp_integration.py"),
    ("cumulative token usage and cost totals", "usage.py"),
    ("show the diff and require the user to type yes", "approvals.py"),
    ("validate task plan steps only one in progress", "planning.py"),
    ("normalize strands events into text deltas and tool started", "streaming.py"),
    ("read only git diff excluding protected paths", "git_tools.py"),
]


def test_tokenizer_splits_identifiers_and_drops_stopwords() -> None:
    assert tokenize("loadSessionStore the_cache HTTPServer") == [
        "load",
        "session",
        "store",
        "cache",
        "http",
        "server",
    ]


def _hit_rate(search) -> float:
    hits = 0
    for question, expected in BENCHMARK:
        if expected in search(question):
            hits += 1
    return hits / len(BENCHMARK)


def test_ranked_retrieval_beats_phrase_search_on_this_repository() -> None:
    workspace = Workspace(SRC)
    retriever = WorkspaceRetriever(workspace=workspace, k=3)

    def ranked(question: str) -> list[str]:
        return [Path(doc.metadata["path"]).name for doc in retriever.invoke(question)]

    def phrase(question: str) -> list[str]:
        # The existing search_code tool: the whole question must appear on one line.
        names = []
        for line in workspace.search(question):
            names.append(Path(line.split(":", 1)[0]).name)
        return names

    ranked_rate, phrase_rate = _hit_rate(ranked), _hit_rate(phrase)
    print(f"hit@3 ranked={ranked_rate:.2f} phrase={phrase_rate:.2f}")
    assert phrase_rate == 0.0
    assert ranked_rate >= 0.8


def test_retriever_never_returns_protected_files(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def charge_card():\n    return 'public'\n")
    (tmp_path / ".env").write_text("charge_card=SECRET_VALUE\n" * 20)
    (tmp_path / "id_rsa").write_text("charge_card PRIVATE KEY\n")
    docs = WorkspaceRetriever(workspace=Workspace(tmp_path)).invoke("charge card")
    assert [doc.metadata["path"] for doc in docs] == ["app.py"]
    assert "SECRET" not in str(docs) and "PRIVATE" not in str(docs)


def test_retriever_rejects_empty_or_huge_questions(tmp_path: Path) -> None:
    retriever = WorkspaceRetriever(workspace=Workspace(tmp_path))
    with pytest.raises(WorkspaceError):
        retriever.invoke("   ")
    with pytest.raises(WorkspaceError):
        retriever.invoke("x" * 501)
