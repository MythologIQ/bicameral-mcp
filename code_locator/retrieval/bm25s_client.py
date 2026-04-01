"""BM25 search using the bm25s library.

Adapted from tools/bicameral-locagent/dependency_graph/build_graph.py::build_bm25_index().
File-level granularity: each document is a source file, scored by BM25.
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path

from ..indexing.index_builder import iter_source_files
from ..models import RetrievalResult
from .bm25_protocol import BM25Search


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


class Bm25sClient(BM25Search):
    """BM25 search backed by the bm25s library."""

    def __init__(self) -> None:
        self._bm25 = None
        self._doc_ids: list[str] = []
        self._loaded = False

    def index(self, repo_path: str, output_dir: str) -> None:
        """Build BM25 index from source files and persist to disk."""
        import bm25s

        documents: list[str] = []
        doc_ids: list[str] = []
        for rel_path, abs_path in iter_source_files(repo_path):
            documents.append(_read_file(abs_path))
            doc_ids.append(rel_path)

        if not documents:
            self._bm25 = bm25s.BM25()
            self._doc_ids = []
            self._loaded = True
            return

        tokens = bm25s.tokenize(documents, stopwords="en", show_progress=False)
        bm25 = bm25s.BM25()
        bm25.index(tokens, show_progress=False)

        os.makedirs(output_dir, exist_ok=True)
        index_path = Path(output_dir) / "bm25_index.pkl"
        with open(index_path, "wb") as f:
            pickle.dump({"bm25": bm25, "doc_ids": doc_ids}, f)

        self._bm25 = bm25
        self._doc_ids = doc_ids
        self._loaded = True

    def load(self, index_dir: str) -> None:
        """Load a previously built BM25 index from disk."""
        index_path = Path(index_dir) / "bm25_index.pkl"
        if not index_path.exists():
            raise FileNotFoundError(f"BM25 index not found at {index_path}")

        with open(index_path, "rb") as f:
            data = pickle.load(f)

        self._bm25 = data["bm25"]
        self._doc_ids = data["doc_ids"]
        self._loaded = True

    def search(self, query: str, num_results: int = 20) -> list[RetrievalResult]:
        """Search the BM25 index for relevant files."""
        if not self._loaded or self._bm25 is None or not self._doc_ids:
            return []

        import bm25s

        tokens = bm25s.tokenize([query], stopwords="en", show_progress=False)
        k = min(num_results, len(self._doc_ids))
        results, scores = self._bm25.retrieve(tokens, k=k)

        # Patterns for test/spec files — exclude entirely so they never become grounding candidates
        _TEST_PREFIXES = ("test/", "tests/", "spec/", "__tests__/", "test_", "tests_")
        _TEST_SUFFIXES = ("_test.py", "_test.ts", "_spec.py", "_spec.ts")

        output: list[RetrievalResult] = []
        for i in range(k):
            doc_idx = results[0, i]
            score = float(scores[0, i])
            if score <= 0:
                continue
            file_path = self._doc_ids[doc_idx]
            if any(file_path.startswith(p) or f"/{p}" in file_path for p in _TEST_PREFIXES) \
                    or any(file_path.endswith(s) for s in _TEST_SUFFIXES):
                continue
            output.append(
                RetrievalResult(
                    file_path=file_path,
                    line_number=0,
                    snippet="",
                    score=score,
                    method="bm25",
                )
            )
        output.sort(key=lambda r: r.score, reverse=True)
        return output

    @property
    def is_loaded(self) -> bool:
        return self._loaded
