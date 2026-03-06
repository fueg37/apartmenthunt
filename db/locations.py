from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite

from models import Location, LocationType, location_from_row


async def insert(db: aiosqlite.Connection, loc: Location) -> int:
    extra = _build_extra(loc)
    cur = await db.execute(
        """
        INSERT INTO locations
            (name, address, lat, lon, location_type, rating, review_count,
             website_url, phone, is_top_pick, notes, extra_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            address=excluded.address,
            lat=excluded.lat,
            lon=excluded.lon,
            rating=excluded.rating,
            review_count=excluded.review_count,
            website_url=excluded.website_url,
            phone=excluded.phone,
            is_top_pick=excluded.is_top_pick,
            notes=excluded.notes,
            extra_json=excluded.extra_json
        """,
        (
            loc.name, loc.address, loc.lat, loc.lon,
            loc.location_type.value, loc.rating, loc.review_count,
            loc.website_url, loc.phone, int(loc.is_top_pick),
            loc.notes, json.dumps(extra),
        ),
    )
    await db.commit()
    return cur.lastrowid or await _get_id_by_name(db, loc.name)


async def _get_id_by_name(db: aiosqlite.Connection, name: str) -> int:
    cur = await db.execute("SELECT id FROM locations WHERE name=?", (name,))
    row = await cur.fetchone()
    return row["id"] if row else -1


async def get_by_name(db: aiosqlite.Connection, name: str) -> Location | None:
    cur = await db.execute("SELECT * FROM locations WHERE name=?", (name,))
    row = await cur.fetchone()
    return location_from_row(dict(row)) if row else None


async def get_by_id(db: aiosqlite.Connection, loc_id: int) -> Location | None:
    cur = await db.execute("SELECT * FROM locations WHERE id=?", (loc_id,))
    row = await cur.fetchone()
    return location_from_row(dict(row)) if row else None


async def list_all(
    db: aiosqlite.Connection,
    location_type: LocationType | None = None,
) -> list[Location]:
    if location_type:
        cur = await db.execute(
            "SELECT * FROM locations WHERE location_type=? ORDER BY name",
            (location_type.value,),
        )
    else:
        cur = await db.execute("SELECT * FROM locations ORDER BY location_type, name")
    rows = await cur.fetchall()
    return [location_from_row(dict(r)) for r in rows]


async def mark_scraped(
    db: aiosqlite.Connection,
    loc_id: int,
    success: bool,
    error_msg: str | None = None,
) -> None:
    now = datetime.utcnow().isoformat()
    await db.execute(
        "UPDATE locations SET last_scraped=?, scraped_ok=? WHERE id=?",
        (now, int(success), loc_id),
    )
    await db.execute(
        "INSERT INTO scrape_events (location_id, scraped_at, success, error_msg) VALUES (?,?,?,?)",
        (loc_id, now, int(success), error_msg),
    )
    await db.commit()


def _build_extra(loc: Location) -> dict[str, Any]:
    """Merge location-type-specific fields into the extra dict."""
    extra = dict(loc.extra)
    from models import Apartment, Gym, Hospital, PointOfInterest
    if isinstance(loc, Apartment) and loc.apartments_com_slug:
        extra["apartments_com_slug"] = loc.apartments_com_slug
    if isinstance(loc, (Gym, Hospital)):
        if loc.google_place_id:
            extra["google_place_id"] = loc.google_place_id
        if loc.hours:
            extra["hours"] = loc.hours
    if isinstance(loc, Gym) and loc.equipment_highlights:
        extra["equipment_highlights"] = loc.equipment_highlights
    if isinstance(loc, Hospital) and loc.health_system:
        extra["health_system"] = loc.health_system
    if isinstance(loc, PointOfInterest) and loc.category:
        extra["category"] = loc.category
    return extra
