"""Handler for /ingest MCP tool.

Productized ingestion entrypoint:
- accepts a normalized payload shaped like the internal CodeLocatorPayload handoff
- writes decisions/code regions into the ledger
- records a source cursor so Slack / Notion / other upstream sources can sync incrementally
"""

from __future__ import annotations

import os
import logging

from adapters.ledger import get_ledger
from contracts import IngestResponse, IngestStats, SourceCursorSummary

logger = logging.getLogger(__name__)


# Threshold from Decision Ledger Standard — llm provenance range 0.3–0.7, default gate 0.5.
# Replaced with eval-calibrated value once Silong's RAG eval runs.
AUTO_GROUND_THRESHOLD = 0.5


def _auto_ground_via_search(mappings: list[dict], repo: str) -> list[dict]:
    """For mappings with no code_regions and no symbols, run BM25 search on the
    intent description and backfill code_regions from the top-scoring file's symbols.

    This is the fallback for transcript-only ingestion where no symbol names are known.
    Uses a hardcoded threshold of 0.5 — replace once eval pins the right value.
    """
    db_path = os.getenv("CODE_LOCATOR_SQLITE_DB", "")
    if not db_path:
        db_path = str(os.path.join(repo, ".bicameral", "code-graph.db"))

    try:
        from adapters.code_locator import get_code_locator
        from code_locator.indexing.sqlite_store import SymbolDB
        locator = get_code_locator()
        db = SymbolDB(db_path)
    except Exception as exc:
        logger.warning("[ingest] auto-ground unavailable: %s", exc)
        return mappings

    resolved = []
    for mapping in mappings:
        if mapping.get("code_regions") or mapping.get("symbols"):
            resolved.append(mapping)
            continue

        description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
        if not description:
            resolved.append(mapping)
            continue

        try:
            hits = locator.search_code(description)
        except Exception as exc:
            logger.warning("[ingest] search_code failed for '%s': %s", description[:60], exc)
            resolved.append(mapping)
            continue

        # Take the top hit above threshold — file-level BM25, so we expand to its symbols
        top = next((h for h in hits if h.get("score", 0) >= AUTO_GROUND_THRESHOLD), None)
        if not top:
            logger.debug("[ingest] no confident match for: %s", description[:60])
            resolved.append(mapping)
            continue

        file_path = top["file_path"]
        symbols = db.lookup_by_file(file_path)
        if not symbols:
            resolved.append(mapping)
            continue

        code_regions = [
            {
                "symbol": row["qualified_name"] or row["name"],
                "file_path": row["file_path"],
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "type": row["type"],
                "purpose": description,
            }
            for row in symbols[:5]  # cap at 5 symbols per file
        ]
        logger.info(
            "[ingest] auto-grounded '%s' → %s (%d symbols, score=%.2f)",
            description[:60], file_path, len(code_regions), top["score"],
        )
        resolved.append({**mapping, "code_regions": code_regions})

    return resolved


def _resolve_symbols_to_regions(payload: dict, repo: str) -> dict:
    """For each mapping with symbols[] but no code_regions, look up symbol names
    in the code graph and populate code_regions from the results."""
    mappings = payload.get("mappings")
    if not mappings:
        return payload

    needs_resolution = any(
        m.get("symbols") and not m.get("code_regions")
        for m in mappings
    )
    if not needs_resolution:
        return payload

    db_path = os.getenv("CODE_LOCATOR_SQLITE_DB", "")
    if not db_path:
        import os as _os
        db_path = str(_os.path.join(repo, ".bicameral", "code-graph.db"))

    try:
        from code_locator.indexing.sqlite_store import SymbolDB
        db = SymbolDB(db_path)
    except Exception as exc:
        logger.warning("[ingest] cannot open symbol DB at %s: %s", db_path, exc)
        return payload

    resolved_mappings = []
    for mapping in mappings:
        symbol_names = mapping.get("symbols") or []
        code_regions = mapping.get("code_regions") or []

        if symbol_names and not code_regions:
            for name in symbol_names:
                rows = db.lookup_by_name(name)
                for row in rows:
                    code_regions.append({
                        "symbol": row["qualified_name"] or row["name"],
                        "file_path": row["file_path"],
                        "start_line": row["start_line"],
                        "end_line": row["end_line"],
                        "type": row["type"],
                        "purpose": mapping.get("intent", ""),
                    })
            if code_regions:
                mapping = {**mapping, "code_regions": code_regions}
            else:
                logger.debug("[ingest] no symbols found in index for: %s", symbol_names)

        resolved_mappings.append(mapping)

    return {**payload, "mappings": resolved_mappings}


def _derive_last_source_ref(payload: dict) -> str:
    mappings = payload.get("mappings") or []
    refs = [str((m.get("span") or {}).get("source_ref", "")).strip() for m in mappings]
    refs = [ref for ref in refs if ref]
    return refs[-1] if refs else str(payload.get("query", "")).strip()


async def handle_ingest(
    payload: dict,
    source_scope: str = "",
    cursor: str = "",
) -> IngestResponse:
    ledger = get_ledger()
    if hasattr(ledger, "connect"):
        await ledger.connect()

    repo = str(payload.get("repo") or os.getenv("REPO_PATH", "."))
    payload = _resolve_symbols_to_regions(payload, repo)
    mappings = _auto_ground_via_search(payload.get("mappings") or [], repo)
    payload = {**payload, "mappings": mappings}
    result = await ledger.ingest_payload(payload)

    cursor_summary = None
    source_type = str(((payload.get("mappings") or [{}])[0].get("span") or {}).get("source_type", "manual"))
    last_source_ref = _derive_last_source_ref(payload)
    if hasattr(ledger, "upsert_source_cursor"):
        cursor_row = await ledger.upsert_source_cursor(
            repo=repo,
            source_type=source_type,
            source_scope=source_scope or "default",
            cursor=cursor or last_source_ref,
            last_source_ref=last_source_ref,
        )
        cursor_summary = SourceCursorSummary(**cursor_row)

    source_refs = []
    for mapping in payload.get("mappings", []):
        span = mapping.get("span") or {}
        ref = str(span.get("source_ref", "")).strip()
        if ref and ref not in source_refs:
            source_refs.append(ref)

    stats = result.get("stats", {})
    return IngestResponse(
        ingested=bool(result.get("ingested", False)),
        repo=str(result.get("repo", repo)),
        query=str(payload.get("query", "")),
        source_refs=source_refs,
        stats=IngestStats(
            intents_created=int(stats.get("intents_created", 0)),
            symbols_mapped=int(stats.get("symbols_mapped", 0)),
            regions_linked=int(stats.get("regions_linked", 0)),
            ungrounded=int(stats.get("ungrounded", 0)),
        ),
        ungrounded_intents=list(result.get("ungrounded_intents", [])),
        source_cursor=cursor_summary,
    )
