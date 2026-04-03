from __future__ import annotations

from datetime import datetime

import aiosqlite

from models import Unit


async def upsert_units(
    db: aiosqlite.Connection,
    location_id: int,
    units: list[Unit],
) -> None:
    """Replace the latest scraped unit snapshot for a location with fresh data.

    Manual units (is_manual=1) are never touched by this function.
    """
    now = datetime.utcnow().isoformat()
    # Mark only non-manual units as unavailable, preserving manual data.
    await db.execute(
        """
        UPDATE units SET available=0
        WHERE location_id=? AND is_manual=0
          AND scraped_at=(SELECT MAX(scraped_at) FROM units WHERE location_id=? AND is_manual=0)
        """,
        (location_id, location_id),
    )
    for unit in units:
        await db.execute(
            """
            INSERT INTO units
                (location_id, floor_plan_name, bed, bath, sqft_min, sqft_max,
                 price_min, price_max, available, move_in_date, unit_number, is_manual, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)
            """,
            (
                location_id,
                unit.floor_plan_name,
                unit.bed,
                unit.bath,
                unit.sqft_min,
                unit.sqft_max,
                unit.price_min,
                unit.price_max,
                int(unit.available),
                unit.move_in_date.isoformat() if unit.move_in_date else None,
                unit.unit_number,
                now,
            ),
        )
    await db.commit()


async def save_manual_units(
    db: aiosqlite.Connection,
    location_id: int,
    units: list[Unit],
) -> None:
    """Replace all manually-entered units for a location.

    Deletes existing manual units and inserts the new set. Scraped units are untouched.
    """
    now = datetime.utcnow().isoformat()
    await db.execute(
        "DELETE FROM units WHERE location_id=? AND is_manual=1",
        (location_id,),
    )
    for unit in units:
        await db.execute(
            """
            INSERT INTO units
                (location_id, floor_plan_name, bed, bath, sqft_min, sqft_max,
                 price_min, price_max, available, move_in_date, unit_number, is_manual, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,1,?,?,1,?)
            """,
            (
                location_id,
                unit.floor_plan_name,
                unit.bed,
                unit.bath,
                unit.sqft_min,
                unit.sqft_max,
                unit.price_min,
                unit.price_max,
                unit.move_in_date.isoformat() if unit.move_in_date else None,
                unit.unit_number,
                now,
            ),
        )
    await db.commit()


async def delete_manual_units(
    db: aiosqlite.Connection,
    location_id: int,
) -> None:
    """Delete all manually-entered units for a location (re-enables auto-scraping)."""
    await db.execute(
        "DELETE FROM units WHERE location_id=? AND is_manual=1",
        (location_id,),
    )
    await db.commit()


async def has_manual_units(
    db: aiosqlite.Connection,
    location_id: int,
) -> bool:
    """Return True if the location has any manually-entered unit data."""
    cur = await db.execute(
        "SELECT 1 FROM units WHERE location_id=? AND is_manual=1 LIMIT 1",
        (location_id,),
    )
    return await cur.fetchone() is not None


async def get_latest_units(
    db: aiosqlite.Connection,
    location_id: int,
) -> list[Unit]:
    """Fetch all manual units + the most recent scraped snapshot for a location."""
    cur = await db.execute(
        """
        SELECT * FROM units
        WHERE location_id=?
          AND (
            is_manual=1
            OR scraped_at=(SELECT MAX(scraped_at) FROM units WHERE location_id=? AND is_manual=0)
          )
        ORDER BY is_manual DESC, bed, bath, price_min
        """,
        (location_id, location_id),
    )
    rows = await cur.fetchall()
    return [_unit_from_row(dict(r)) for r in rows]


async def get_previous_units(
    db: aiosqlite.Connection,
    location_id: int,
) -> list[Unit]:
    """Fetch the second-most-recent scraped snapshot (for diffing)."""
    cur = await db.execute(
        """
        SELECT DISTINCT scraped_at FROM units
        WHERE location_id=? AND is_manual=0
        ORDER BY scraped_at DESC
        LIMIT 2
        """,
        (location_id,),
    )
    timestamps = [r[0] for r in await cur.fetchall()]
    if len(timestamps) < 2:
        return []
    prev_ts = timestamps[1]
    cur = await db.execute(
        "SELECT * FROM units WHERE location_id=? AND scraped_at=? AND is_manual=0 ORDER BY bed, bath, price_min",
        (location_id, prev_ts),
    )
    rows = await cur.fetchall()
    return [_unit_from_row(dict(r)) for r in rows]


def _unit_from_row(row: dict) -> Unit:
    from datetime import date as _date
    move_in = None
    if row.get("move_in_date"):
        try:
            move_in = _date.fromisoformat(row["move_in_date"])
        except ValueError:
            pass
    scraped_at = None
    if row.get("scraped_at"):
        try:
            scraped_at = datetime.fromisoformat(row["scraped_at"])
        except ValueError:
            pass
    return Unit(
        floor_plan_name=row["floor_plan_name"],
        bed=row["bed"],
        bath=row["bath"],
        sqft_min=row.get("sqft_min"),
        sqft_max=row.get("sqft_max"),
        price_min=row.get("price_min"),
        price_max=row.get("price_max"),
        available=bool(row.get("available", 1)),
        move_in_date=move_in,
        unit_number=row.get("unit_number"),
        is_manual=bool(row.get("is_manual", 0)),
        scraped_at=scraped_at,
    )
