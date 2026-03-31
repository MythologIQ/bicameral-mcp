"""
Desynchronization scenario registry.

Maps to the 12 scenarios from "The Auto-Grounding Problem" Notion doc.
Each scenario defines: what breaks, what the expected behavior is,
and whether the current implementation handles it.

This is the living artifact — as fixes land, update `currently_passing`
to track progress toward 80% reliability.
"""

DESYNC_SCENARIOS = [
    {
        "id": "desync_01",
        "name": "New decision, no auto-link",
        "description": "Decision ingested with empty code_regions, matching code exists in repo",
        "expected_status": "pending",  # Should auto-ground
        "current_behavior": "ungrounded",  # No auto-grounding implemented
        "severity": "P0",
        "currently_passing": False,
    },
    {
        "id": "desync_02",
        "name": "Code changed after grounding",
        "description": "Code region modified after decision was grounded — content_hash mismatch",
        "expected_status": "drifted",
        "current_behavior": "drifted",
        "severity": "OK",
        "currently_passing": True,
    },
    {
        "id": "desync_03",
        "name": "Code deleted after grounding",
        "description": "File containing grounded code region is deleted",
        "expected_status": "pending",
        "current_behavior": "pending",
        "severity": "OK",
        "currently_passing": True,
    },
    {
        "id": "desync_04",
        "name": "Symbol renamed",
        "description": "Function renamed from process_payment() to handle_payment() — old maps_to edge stale",
        "expected_status": "drifted",  # Should detect stale link
        "current_behavior": "silent stale link",
        "severity": "P1",
        "currently_passing": False,
    },
    {
        "id": "desync_05",
        "name": "Symbol moved to different file",
        "description": "Symbol moved from payments/handler.py to billing/processor.py — code_region.file_path wrong",
        "expected_status": "drifted",  # Should detect file_path mismatch
        "current_behavior": "content_hash fails silently",
        "severity": "P1",
        "currently_passing": False,
    },
    {
        "id": "desync_06",
        "name": "Index rebuilt, new symbols",
        "description": "Code index rebuilt after new code added — ungrounded intents could now match",
        "expected_status": "pending",  # Should re-ground
        "current_behavior": "stays ungrounded",
        "severity": "P0",
        "currently_passing": False,
    },
    {
        "id": "desync_07",
        "name": "Cold start, no index",
        "description": "Fresh repo, no code index — everything should be ungrounded until index built",
        "expected_status": "ungrounded",
        "current_behavior": "ungrounded",
        "severity": "P0",
        "currently_passing": True,  # Partial — behavior is correct but no bootstrap
    },
    {
        "id": "desync_08",
        "name": "Drifted but recoverable",
        "description": "Code moved to new location but intent description still matches — should re-ground",
        "expected_status": "reflected",  # After re-ground
        "current_behavior": "stays drifted",
        "severity": "P1",
        "currently_passing": False,
    },
    {
        "id": "desync_09",
        "name": "Intent supersession",
        "description": "Updated version of same decision ingested — old should be superseded",
        "expected_status": "superseded",
        "current_behavior": "duplicate intent created",
        "severity": "P2",
        "currently_passing": False,
    },
    {
        "id": "desync_10",
        "name": "Multiple intents, same symbol",
        "description": "Two decisions both map to same function — both should update on code change",
        "expected_status": "both_drifted",
        "current_behavior": "both_drifted",
        "severity": "OK",
        "currently_passing": True,
    },
    {
        "id": "desync_11",
        "name": "False positive grounding",
        "description": "BM25 matches test file (rate_limiter_test.py) instead of implementation",
        "expected_status": "no_false_ground",
        "current_behavior": "threshold helps but not perfect",
        "severity": "P2",
        "currently_passing": True,  # Partial — confidence threshold helps
    },
    {
        "id": "desync_12",
        "name": "Line numbers shift",
        "description": "Insertion above tracked region shifts start_line/end_line — hash breaks",
        "expected_status": "reflected",  # Logic unchanged, just shifted
        "current_behavior": "drifted (false positive)",
        "severity": "P1",
        "currently_passing": False,
    },
]

# Convenience views
BY_SEVERITY = {}
for s in DESYNC_SCENARIOS:
    BY_SEVERITY.setdefault(s["severity"], []).append(s)

CURRENTLY_FAILING = [s for s in DESYNC_SCENARIOS if not s["currently_passing"]]
CURRENTLY_PASSING = [s for s in DESYNC_SCENARIOS if s["currently_passing"]]
