"""Helpers for recording and querying apartment score runs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

_SCORE_RUN_STALE_AFTER = timedelta(hours=24)

_SCORE_RUN_SELECT = """
SELECT id, apartment_id, profile_id, score, tier, confidence,
       eligibility_passed, breakdown_json, reasons_json, computed_at
  FROM score_runs
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _hydrate_score_run_row(row: aiosqlite.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["eligibility_passed"] = bool(payload.get("eligibility_passed"))
    payload["breakdown_json"] = _loads_json(payload.get("breakdown_json"), {})
    payload["reasons_json"] = _loads_json(payload.get("reasons_json"), [])
    return payload


async def insert_score_run(
    db: aiosqlite.Connection,
    apartment_id: int,
    profile_id: int,
    score: int,
    tier: str,
    confidence: str | None,
    eligibility_passed: bool,
    breakdown_json: dict[str, Any],
    reasons_json: list[str],
    computed_at: str,
) -> int:
    cur = await db.execute(
        """
        INSERT INTO score_runs(
            apartment_id, profile_id, score, tier, confidence, eligibility_passed,
            breakdown_json, reasons_json, computed_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            apartment_id,
            profile_id,
            int(score),
            tier,
            confidence,
            1 if eligibility_passed else 0,
            _dumps(breakdown_json),
            _dumps(reasons_json),
            computed_at,
        ),
    )
    await db.commit()
    return cur.lastrowid


async def latest_score_run(
    db: aiosqlite.Connection,
    apartment_id: int,
    profile_id: int,
) -> dict[str, Any] | None:
    cur = await db.execute(
        f"""
        {_SCORE_RUN_SELECT}
         WHERE apartment_id=? AND profile_id=?
         ORDER BY computed_at DESC, id DESC
         LIMIT 1
        """,
        (apartment_id, profile_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    return _hydrate_score_run_row(row)


async def should_insert_score_run(
    db: aiosqlite.Connection,
    apartment_id: int,
    profile_id: int,
    score: int,
    tier: str,
    confidence: str | None,
    eligibility_passed: bool,
    breakdown_json: dict[str, Any],
    reasons_json: list[str],
    computed_at: str,
    stale_after: timedelta = _SCORE_RUN_STALE_AFTER,
) -> bool:
    latest = await latest_score_run(db, apartment_id, profile_id)
    if not latest:
        return True

    changed = any(
        [
            int(latest["score"]) != int(score),
            latest["tier"] != tier,
            (latest.get("confidence") or "") != (confidence or ""),
            bool(latest.get("eligibility_passed")) != bool(eligibility_passed),
            latest.get("breakdown_json") != breakdown_json,
            latest.get("reasons_json") != reasons_json,
        ]
    )
    if changed:
        return True

    try:
        latest_at = datetime.fromisoformat(str(latest["computed_at"]))
        computed_dt = datetime.fromisoformat(computed_at)
    except Exception:
        return True

    return (computed_dt - latest_at) >= stale_after


async def save_score_run_if_changed(
    db: aiosqlite.Connection,
    apartment_id: int,
    profile_id: int | None,
    score: int,
    tier: str,
    confidence: str | None,
    eligibility_passed: bool,
    breakdown_json: dict[str, Any],
    reasons_json: list[str],
    computed_at: str | None = None,
    stale_after: timedelta = _SCORE_RUN_STALE_AFTER,
) -> int | None:
    """Persist a score run for an apartment/profile only when changed or stale."""
    if not profile_id:
        return None

    computed = computed_at or datetime.utcnow().isoformat()
    should_insert = await should_insert_score_run(
        db=db,
        apartment_id=apartment_id,
        profile_id=profile_id,
        score=score,
        tier=tier,
        confidence=confidence,
        eligibility_passed=eligibility_passed,
        breakdown_json=breakdown_json,
        reasons_json=reasons_json,
        computed_at=computed,
        stale_after=stale_after,
    )
    if not should_insert:
        return None

    return await insert_score_run(
        db=db,
        apartment_id=apartment_id,
        profile_id=profile_id,
        score=score,
        tier=tier,
        confidence=confidence,
        eligibility_passed=eligibility_passed,
        breakdown_json=breakdown_json,
        reasons_json=reasons_json,
        computed_at=computed,
    )


async def list_score_history(
    db: aiosqlite.Connection,
    apartment_id: int,
    profile_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    cur = await db.execute(
        f"""
        {_SCORE_RUN_SELECT}
         WHERE apartment_id=? AND profile_id=?
         ORDER BY computed_at DESC, id DESC
         LIMIT ?
        """,
        (apartment_id, profile_id, limit),
    )
    rows = await cur.fetchall()
    return [_hydrate_score_run_row(row) for row in rows]


def with_score_history_diffs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate score runs with diffs against the previous run in time."""
    annotated: list[dict[str, Any]] = []
    for idx, run in enumerate(runs):
        enriched = {**run}
        prev = runs[idx + 1] if idx + 1 < len(runs) else None
        if not prev:
            enriched["diff_from_previous"] = None
            annotated.append(enriched)
            continue

        current_reasons = run.get("reasons_json") or []
        prev_reasons = prev.get("reasons_json") or []
        diff: dict[str, Any] = {
            "score_delta": int(run.get("score", 0)) - int(prev.get("score", 0)),
            "tier_changed": run.get("tier") != prev.get("tier"),
            "eligibility_changed": bool(run.get("eligibility_passed")) != bool(prev.get("eligibility_passed")),
            "confidence_changed": (run.get("confidence") or "") != (prev.get("confidence") or ""),
            "added_reasons": [r for r in current_reasons if r not in prev_reasons],
            "removed_reasons": [r for r in prev_reasons if r not in current_reasons],
            "breakdown_changed": run.get("breakdown_json") != prev.get("breakdown_json"),
        }
        diff["changed"] = any(
            [
                diff["score_delta"] != 0,
                diff["tier_changed"],
                diff["eligibility_changed"],
                diff["confidence_changed"],
                bool(diff["added_reasons"]),
                bool(diff["removed_reasons"]),
                diff["breakdown_changed"],
            ]
        )
        enriched["diff_from_previous"] = diff
        annotated.append(enriched)
    return annotated
