"""Ranked code retrieval as a LangChain ``BaseRetriever`` over the guarded workspace.

Plain substring search needs the exact phrase; a natural-language question rarely contains it.
This retriever chunks accessible files and ranks chunks with BM25 over identifier-aware tokens.
It reads only through ``Workspace`` so protected files and links stay unreachable. It is lexical
on purpose: semantic embeddings would send repository text to a model provider.
"""

import math
import re
from collections import Counter
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from code_harness.workspace import Workspace, WorkspaceError

CHUNK_LINES = 40
CHUNK_OVERLAP = 10
MAX_RESULTS = 8
MAX_QUERY_CHARS = 500
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "is",
        "are",
        "was",
        "be",
        "and",
        "or",
        "for",
        "with",
        "how",
        "what",
        "where",
        "which",
        "does",
        "do",
        "it",
        "this",
        "that",
        "from",
        "on",
        "by",
        "as",
        "at",
        "into",
        "when",
        "who",
        "why",
        "can",
        "i",
        "we",
        "you",
        "my",
        "our",
    ]
)
_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str) -> list[str]:
    """Split identifiers (snake_case, camelCase) into lowercase words and drop stopwords."""
    tokens: list[str] = []
    for word in _WORDS.findall(text):
        parts = [part.lower() for part in _CAMEL.findall(word)] or [word.lower()]
        tokens.extend(part for part in parts if part not in STOPWORDS and len(part) > 1)
    return tokens


class WorkspaceRetriever(BaseRetriever):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace: Workspace
    k: int = 5

    def _chunks(self) -> list[tuple[str, int, str]]:
        chunks: list[tuple[str, int, str]] = []
        for path in self.workspace.files():
            try:
                lines = self.workspace.read(path).splitlines()
            except WorkspaceError:
                continue
            step = CHUNK_LINES - CHUNK_OVERLAP
            for start in range(0, max(len(lines), 1), step):
                text = "\n".join(lines[start : start + CHUNK_LINES])
                if text.strip():
                    chunks.append((path, start + 1, text))
                if start + CHUNK_LINES >= len(lines):
                    break
        return chunks

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun, **kwargs: Any
    ) -> list[Document]:
        if not query.strip() or len(query) > MAX_QUERY_CHARS:
            raise WorkspaceError("Question must contain 1 to 500 characters")
        terms = set(tokenize(query))
        if not terms:
            return []
        chunks = self._chunks()
        # The path is searchable text too, so "session store" finds sessions.py.
        docs = [Counter(tokenize(path) * 2 + tokenize(text)) for path, _, text in chunks]
        if not docs:
            return []
        average = sum(sum(doc.values()) for doc in docs) / len(docs)
        frequency = Counter(term for doc in docs for term in terms if term in doc)
        scored: list[tuple[float, int]] = []
        for index, doc in enumerate(docs):
            size = sum(doc.values())
            score = 0.0
            for term in terms:
                tf = doc.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (len(docs) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * size / average))
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        results: list[Document] = []
        seen: set[tuple[str, int]] = set()
        for score, index in scored:
            path, start, text = chunks[index]
            if (path, start // CHUNK_LINES) in seen:
                continue  # overlapping chunks of one region add noise
            seen.add((path, start // CHUNK_LINES))
            results.append(
                Document(
                    page_content=text[:2000],
                    metadata={"path": path, "start_line": start, "score": round(score, 3)},
                )
            )
            if len(results) >= min(self.k, MAX_RESULTS):
                break
        return results


def format_documents(documents: list[Document]) -> str:
    if not documents:
        return "No relevant code found."
    return "\n\n".join(
        f"{doc.metadata['path']}:{doc.metadata['start_line']} (score {doc.metadata['score']})\n"
        f"{doc.page_content}"
        for doc in documents
    )
