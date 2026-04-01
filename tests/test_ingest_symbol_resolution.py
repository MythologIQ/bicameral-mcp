"""
Regression tests for ingest symbol resolution gaps found in trial run (2026-04-01).

Bug 1: `symbols` field in ingest payload is silently ignored — symbols_mapped always 0
Bug 2: Stale indexed_files entries with symbol_count=0 block re-indexing
"""
import os
import tempfile
import shutil
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("USE_REAL_LEDGER", "1")
os.environ.setdefault("SURREAL_URL", "memory://")

from adapters.ledger import get_ledger, reset_ledger_singleton
from handlers.ingest import handle_ingest
from handlers.decision_status import handle_decision_status
from code_locator.indexing.index_builder import build_index
from code_locator.indexing.sqlite_store import SymbolDB


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    reset_ledger_singleton()
    yield
    reset_ledger_singleton()


@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    """Create a temp repo with source files, build symbol + BM25 index.

    DB is placed at {repo}/.bicameral/code-graph.db — matching ensure_runtime_env()
    so get_code_locator() works without extra env overrides.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    bicameral_dir = repo / ".bicameral"
    bicameral_dir.mkdir()

    # Write Python files with known symbols
    (repo / "payments").mkdir()
    (repo / "payments" / "handler.py").write_text(
        "def process_payment(amount, currency):\n"
        "    \"\"\"Process a payment.\"\"\"\n"
        "    return {'status': 'ok', 'amount': amount}\n"
        "\n"
        "def process_refund(order_id, reason):\n"
        "    \"\"\"Process a refund.\"\"\"\n"
        "    return {'refunded': True}\n"
    )

    db_path = str(bicameral_dir / "code-graph.db")
    monkeypatch.setenv("REPO_PATH", str(repo))
    # Let ensure_runtime_env derive CODE_LOCATOR_SQLITE_DB from REPO_PATH
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)

    stats = build_index(str(repo), db_path)
    assert stats.symbols_extracted > 0, "Test setup: no symbols extracted — extractor broken"

    # Build BM25 index so search_code works
    from code_locator.retrieval.bm25s_client import Bm25sClient
    bm25 = Bm25sClient()
    bm25.index(str(repo), str(bicameral_dir))

    return str(repo), db_path


# ── Bug 1: symbols field silently ignored ─────────────────────────────

@pytest.mark.asyncio
async def test_ingest_symbols_field_maps_to_code_regions(indexed_repo):
    """
    Regression: passing symbols by name in ingest payload must result in symbols_mapped > 0.
    Previously, the symbols field was ignored and symbols_mapped was always 0.
    """
    repo_path, _ = indexed_repo

    payload = {
        "repo": repo_path,
        "commit_hash": "HEAD",
        "mappings": [
            {
                "span": {
                    "text": "Payment processing handles refunds",
                    "source_type": "transcript",
                    "source_ref": "trial-run-regression",
                },
                "intent": "Payment processing handles refunds",
                "symbols": ["process_refund"],
                "code_regions": [],
            }
        ],
    }

    result = await handle_ingest(payload)

    assert result.stats.symbols_mapped > 0, (
        "symbols field was ignored — symbols_mapped=0 even though 'process_refund' "
        "exists in the index. Fix: resolve symbols[] → code_regions in handle_ingest."
    )
    assert result.stats.regions_linked > 0
    assert result.stats.ungrounded == 0


@pytest.mark.asyncio
async def test_ingest_symbols_field_sets_pending_status(indexed_repo):
    """Intent grounded via symbols field must have status='pending', not 'ungrounded'."""
    repo_path, _ = indexed_repo

    payload = {
        "repo": repo_path,
        "commit_hash": "HEAD",
        "mappings": [
            {
                "span": {
                    "text": "Process payment amounts",
                    "source_type": "transcript",
                    "source_ref": "trial-run-regression",
                },
                "intent": "Process payment amounts",
                "symbols": ["process_payment"],
                "code_regions": [],
            }
        ],
    }

    await handle_ingest(payload)
    status = await handle_decision_status(filter="all")

    matched = [d for d in status.decisions if "process payment" in d.description.lower()]
    assert len(matched) == 1
    assert matched[0].status == "pending", (
        f"Expected 'pending' after symbol grounding, got '{matched[0].status}'"
    )


# ── Bug 1b: auto-ground via BM25 when no symbols provided ─────────────

@pytest.mark.xfail(
    reason="auto-grounding quality is Silong's P0 — skeleton in place, threshold/search tuning pending",
    strict=False,
)
@pytest.mark.asyncio
async def test_ingest_text_only_auto_grounds_via_bm25(indexed_repo, monkeypatch):
    """
    When neither symbols nor code_regions are provided, ingest should auto-ground
    via BM25 search on the intent description. This is the transcript-only case.
    """
    repo_path, db_path = indexed_repo
    monkeypatch.setenv("USE_REAL_CODE_LOCATOR", "1")

    payload = {
        "repo": repo_path,
        "commit_hash": "HEAD",
        "mappings": [
            {
                "span": {
                    "text": "payment processing refund logic",
                    "source_type": "transcript",
                    "source_ref": "auto-ground-regression",
                },
                "intent": "payment processing refund logic",
                # No symbols, no code_regions — pure text-only ingest
            }
        ],
    }

    result = await handle_ingest(payload)

    assert result.stats.symbols_mapped > 0, (
        "Text-only ingest did not auto-ground via BM25. "
        "Expected _auto_ground_via_search to find payments/handler.py and map symbols."
    )
    assert result.stats.ungrounded == 0


@pytest.mark.asyncio
async def test_ingest_unknown_symbol_stays_ungrounded(indexed_repo):
    """If a symbol name doesn't exist in the index, intent should stay ungrounded (not error)."""
    repo_path, _ = indexed_repo

    payload = {
        "repo": repo_path,
        "commit_hash": "HEAD",
        "mappings": [
            {
                "span": {
                    "text": "Some decision about nonexistent code",
                    "source_type": "transcript",
                    "source_ref": "trial-run-regression",
                },
                "intent": "Some decision about nonexistent code",
                "symbols": ["this_function_does_not_exist"],
                "code_regions": [],
            }
        ],
    }

    result = await handle_ingest(payload)
    assert result.stats.ungrounded == 1
    assert result.stats.symbols_mapped == 0


# ── Bug 2: stale indexed_files with symbol_count=0 blocks re-indexing ─

def test_stale_zero_symbol_entry_gets_reindexed(tmp_path):
    """
    Regression: if indexed_files has an entry for a file with symbol_count=0
    but matching mtime, subsequent build_index must re-extract symbols.
    Previously, the mtime-only guard skipped the file leaving it symbol-less forever.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    src = repo / "service.py"
    src.write_text(
        "def my_service_func():\n"
        "    pass\n"
    )

    db_path = str(tmp_path / "code-graph.db")
    db = SymbolDB(db_path)
    db.init_db()

    # Simulate a stale entry: mtime matches but symbol_count=0 (prior failed run)
    import os
    current_mtime = os.path.getmtime(str(src))
    db.upsert_file_record("service.py", current_mtime, symbol_count=0)

    assert db.symbol_count() == 0, "Setup: should start with 0 symbols"

    # Re-run build_index — should detect symbol_count=0 and re-extract
    stats = build_index(str(repo), db_path)

    assert db.symbol_count() > 0, (
        "Stale indexed_files entry with symbol_count=0 blocked re-indexing. "
        "Fix: skip only when mtime matches AND symbol_count > 0."
    )
    assert stats.files_indexed >= 1
