"""CRUD for the discoveries (staging) table."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite


async def insert_discovery(
    db: aiosqlite.Connection,
    *,
    name: str,
    address: str,
    lat: float,
    lon: float,
    location_type: str,
    source: str,
    source_id: str | None,
    data: dict[str, Any],
) -> int | None:
    """Insert a discovered location if not already pending/approved. Returns new row id or None if skipped."""
    # Skip if already discovered (same source + source_id, or same name with pending/approved status)
    if source_id:
        async with db.execute(
            "SELECT id FROM discoveries WHERE source = ? AND source_id = ? AND status != 'rejected'",
            (source, source_id),
        ) as cur:
            if await cur.fetchone():
                return None
    async with db.execute(
        "SELECT id FROM discoveries WHERE name = ? AND status != 'rejected'",
        (name,),
    ) as cur:
        if await cur.fetchone():
            return None

    now = datetime.now(timezone.utc).isoformat()
    async with db.execute(
        """INSERT INTO discoveries (name, address, lat, lon, location_type, source, source_id, data_json, discovered_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (name, address, lat, lon, location_type, source, source_id, json.dumps(data), now),
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def list_pending(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Return all pending discoveries."""
    async with db.execute(
        "SELECT * FROM discoveries WHERE status = 'pending' ORDER BY discovered_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["data"] = json.loads(d.pop("data_json", "{}"))
        result.append(d)
    return result


async def get_by_id(db: aiosqlite.Connection, discovery_id: int) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM discoveries WHERE id = ?", (discovery_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["data"] = json.loads(d.pop("data_json", "{}"))
    return d


async def set_status(db: aiosqlite.Connection, discovery_id: int, status: str) -> bool:
    """Set status to 'approved' or 'rejected'. Returns True if row existed."""
    async with db.execute(
        "UPDATE discoveries SET status = ? WHERE id = ?", (status, discovery_id)
    ) as cur:
        await db.commit()
        return cur.rowcount > 0


async def pending_count(db: aiosqlite.Connection) -> int:
    async with db.execute("SELECT COUNT(*) FROM discoveries WHERE status = 'pending'") as cur:
        row = await cur.fetchone()
        return row[0] if row else 0
