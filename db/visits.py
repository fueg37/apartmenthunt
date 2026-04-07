"""CRUD for the visits table."""
from __future__ import annotations

from typing import Any


async def add_visit(
    db,
    location_id: int,
    visit_date: str,
    impression: int | None,
    notes: str | None,
) -> int:
    cur = await db.execute(
        "INSERT INTO visits (location_id, visit_date, impression, notes) VALUES (?,?,?,?)",
        (location_id, visit_date, impression, notes),
    )
    await db.commit()
    return cur.lastrowid


async def get_visits(db, location_id: int) -> list[dict[str, Any]]:
    cur = await db.execute(
        """SELECT id, location_id, visit_date, impression, notes, created_at
           FROM visits WHERE location_id=? ORDER BY visit_date DESC""",
        (location_id,),
    )
    rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def delete_visit(db, visit_id: int) -> bool:
    cur = await db.execute("DELETE FROM visits WHERE id=?", (visit_id,))
    await db.commit()
    return cur.rowcount > 0
