"""Helpers for recording and querying apartment score runs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import aiosqlite

_SCORE_RUN_STALE_AFTER = timedelta(hours=24)


def _dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _loads_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


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
        """
        SELECT id, apartment_id, profile_id, score, tier, confidence,
               eligibility_passed, breakdown_json, reasons_json, computed_at
          FROM score_runs
         WHERE apartment_id=? AND profile_id=?
         ORDER BY computed_at DESC, id DESC
         LIMIT 1
        """,
        (apartment_id, profile_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    payload = dict(row)
    payload["eligibility_passed"] = bool(payload.get("eligibility_passed"))
    payload["breakdown_json"] = _loads_json(payload.get("breakdown_json"), {})
    payload["reasons_json"] = _loads_json(payload.get("reasons_json"), [])
    return payload


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


async def list_score_history(
    db: aiosqlite.Connection,
    apartment_id: int,
    profile_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    cur = await db.execute(
        """
        SELECT id, apartment_id, profile_id, score, tier, confidence,
               eligibility_passed, breakdown_json, reasons_json, computed_at
          FROM score_runs
         WHERE apartment_id=? AND profile_id=?
         ORDER BY computed_at DESC, id DESC
         LIMIT ?
        """,
        (apartment_id, profile_id, limit),
    )
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        rec["eligibility_passed"] = bool(rec.get("eligibility_passed"))
        rec["breakdown_json"] = _loads_json(rec.get("breakdown_json"), {})
        rec["reasons_json"] = _loads_json(rec.get("reasons_json"), [])
        out.append(rec)
    return out
