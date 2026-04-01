"""Direct sqlite-vec vector search client (Option A).

Replaces VectorSearchClient (which wraps cocoindex-code daemon/CLI)
with direct KNN queries against the sqlite-vec database created by
our CocoIndex pipeline.

Requires: pip install sqlite-vec sentence-transformers
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from ..models import RetrievalResult

logger = logging.getLogger(__name__)

# Test/spec files — excluded from grounding candidates
_TEST_PREFIXES = ("test/", "tests/", "spec/", "__tests__/", "test_", "tests_")
_TEST_SUFFIXES = ("_test.py", "_test.ts", "_spec.py", "_spec.ts")


def _is_test_file(file_path: str) -> bool:
    return (
        any(file_path.startswith(p) or f"/{p}" in file_path for p in _TEST_PREFIXES)
        or any(file_path.endswith(s) for s in _TEST_SUFFIXES)
    )


class SqliteVecClient:
    """Vector search directly against sqlite-vec database.

    Queries the code_embeddings_vec table created by cocoindex_pipeline.py.
    Encodes queries using the same SentenceTransformer model used at index time.
    """

    def __init__(self, db_path: str, embedding_model: str) -> None:
        self._db_path = db_path
        self._model_name = embedding_model
        self._model = None  # lazy-loaded
        self._ready = False

    def load(self, db_path: str | None = None) -> None:
        """Mark the client as ready, optionally updating the db path."""
        if db_path:
            self._db_path = db_path
        self._ready = Path(self._db_path).exists()
        if not self._ready:
            logger.warning("[sqlite-vec] database not found: %s", self._db_path)

    def search(self, query: str, num_results: int = 20) -> list[RetrievalResult]:
        """Encode query and run KNN search against sqlite-vec."""
        if not self._ready:
            return []

        try:
            embedding = self._encode(query)
        except Exception as e:
            logger.warning("[sqlite-vec] encoding failed: %s", e)
            return []

        try:
            return self._knn_search(embedding, num_results)
        except Exception as e:
            logger.warning("[sqlite-vec] search failed: %s", e)
            return []

    def _encode(self, text: str):
        """Encode text to a numpy embedding vector."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
            logger.info("[sqlite-vec] loaded embedding model: %s", self._model_name)
        return self._model.encode(text, normalize_embeddings=True)

    def _knn_search(self, query_embedding, num_results: int) -> list[RetrievalResult]:
        """Run KNN query against the vec0 virtual table."""
        import sqlite_vec

        conn = sqlite3.connect(self._db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Serialize the query embedding for sqlite-vec
        query_blob = sqlite_vec.serialize_float32(query_embedding.tolist())

        try:
            rows = conn.execute(
                """
                SELECT file_path, content, start_line, end_line, distance
                FROM code_embeddings_vec
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (query_blob, num_results),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.warning("[sqlite-vec] query error: %s", e)
            conn.close()
            return []

        results: list[RetrievalResult] = []
        for file_path, content, start_line, end_line, distance in rows:
            # Convert distance to similarity score (cosine distance → similarity)
            score = max(0.0, 1.0 - distance)
            if _is_test_file(file_path):
                score *= 0.3
            results.append(
                RetrievalResult(
                    file_path=file_path,
                    line_number=start_line,
                    snippet=content[:200] if content else "",
                    score=score,
                    method="vector",
                    symbol_name="",
                )
            )

        conn.close()
        results.sort(key=lambda r: r.score, reverse=True)
        return results

    @property
    def is_ready(self) -> bool:
        return self._ready
