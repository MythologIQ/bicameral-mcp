"""Thin async wrapper around the SurrealDB Python SDK.

Handles connection lifecycle, namespace/database selection, and query
result normalization. All callers use `client.query(sql, vars)` and get
back a plain list of dicts — no SDK types leak through.
"""

from __future__ import annotations

import logging
from typing import Any

from surrealdb import AsyncSurreal, RecordID

logger = logging.getLogger(__name__)


def _normalize(value: Any) -> Any:
    """Recursively convert SDK types to plain Python objects."""
    if isinstance(value, RecordID):
        return str(value)  # "intent:abc123"
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


class LedgerClient:
    """Async SurrealDB client for the decision ledger.

    Usage:
        client = LedgerClient("ws://localhost:8001")
        await client.connect()
        rows = await client.query("SELECT * FROM intent")
        await client.close()

    For embedded (testing):
        client = LedgerClient("memory://")
        await client.connect()  # no signin for memory://
    """

    def __init__(
        self,
        url: str = "ws://localhost:8001",
        ns: str = "bicameral",
        db: str = "ledger",
        username: str = "root",
        password: str = "root",
    ) -> None:
        self.url = url
        self.ns = ns
        self.db = db
        self._username = username
        self._password = password
        self._db: Any = None

    async def connect(self) -> None:
        self._db = AsyncSurreal(self.url)
        await self._db.connect()
        # Only sign in for remote servers (ws://, http://) — embedded backends
        # (memory://, surrealkv://) don't need authentication
        if self.url.startswith(("ws://", "wss://", "http://", "https://")):
            await self._db.signin({"username": self._username, "password": self._password})
        await self._db.use(self.ns, self.db)
        logger.info("[ledger] connected to %s/%s/%s", self.url, self.ns, self.db)

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def query(self, sql: str, vars: dict | None = None) -> list[dict]:
        """Run a SurrealQL statement and return a list of normalized dicts."""
        if self._db is None:
            raise RuntimeError("LedgerClient not connected — call await client.connect() first")
        result = await self._db.query(sql, vars)
        return _normalize(result) if isinstance(result, list) else []

    async def execute(self, sql: str, vars: dict | None = None) -> None:
        """Run a SurrealQL statement, discarding the result (DDL / DML)."""
        if self._db is None:
            raise RuntimeError("LedgerClient not connected")
        await self._db.query(sql, vars)

    async def execute_many(self, statements: list[str]) -> None:
        """Run multiple DDL/DML statements in sequence (one at a time)."""
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                await self.execute(stmt)
