from __future__ import annotations

from datetime import datetime

import aiosqlite


async def log_change(
    db: aiosqlite.Connection,
    location_id: int,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> None:
    await db.execute(
        "INSERT INTO change_log (location_id, changed_at, field, old_value, new_value) VALUES (?,?,?,?,?)",
        (location_id, datetime.utcnow().isoformat(), field, old_value, new_value),
    )
    await db.commit()


async def get_changes(
    db: aiosqlite.Connection,
    since: datetime | None = None,
    location_id: int | None = None,
) -> list[dict]:
    conditions = []
    params: list = []
    if since:
        conditions.append("cl.changed_at >= ?")
        params.append(since.isoformat())
    if location_id is not None:
        conditions.append("cl.location_id = ?")
        params.append(location_id)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    cur = await db.execute(
        f"""
        SELECT cl.*, l.name AS location_name
        FROM change_log cl
        JOIN locations l ON l.id = cl.location_id
        {where}
        ORDER BY cl.changed_at DESC
        """,
        params,
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]
