from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

import aiosqlite


DEFAULT_PROFILE_NAME = "Balanced (Default)"
DEFAULT_WEIGHTS = {
    "affordability": 0.35,
    "commute": 0.20,
    "amenities": 0.20,
    "proximity": 0.10,
    "rating": 0.20,
    "priority_signal": 0.05,
    "type_fit": 0.55,
}


async def ensure_default_profile(db: aiosqlite.Connection) -> None:
    """Create a default active profile when the table is empty."""
    cur = await db.execute("SELECT COUNT(1) AS n FROM profiles")
    row = await cur.fetchone()
    if row and row["n"]:
        return

    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        "INSERT INTO profiles(name, is_active, created_at, updated_at) VALUES(?,?,?,?)",
        (DEFAULT_PROFILE_NAME, 1, now, now),
    )
    profile_id = cur.lastrowid
    await db.execute(
        """
        INSERT INTO profile_weights(
            profile_id, affordability_w, commute_w, type_fit_w, space_w,
            amenities_w, proximity_w, quality_w
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            profile_id,
            DEFAULT_WEIGHTS["affordability"],
            DEFAULT_WEIGHTS["commute"],
            DEFAULT_WEIGHTS["type_fit"],
            0.10,
            DEFAULT_WEIGHTS["amenities"],
            DEFAULT_WEIGHTS["proximity"],
            DEFAULT_WEIGHTS["rating"],
        ),
    )
    await db.execute(
        """
        INSERT INTO profile_constraints(
            profile_id, max_true_monthly, max_expected_commute_mins, min_bedrooms,
            required_subtypes_json, required_amenities_json
        ) VALUES (?,?,?,?,?,?)
        """,
        (profile_id, None, None, None, json.dumps([]), json.dumps([])),
    )
    await db.commit()


async def list_profiles(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    await ensure_default_profile(db)
    cur = await db.execute(
        """
        SELECT p.id, p.name, p.is_active, p.created_at, p.updated_at,
               c.max_true_monthly, c.max_expected_commute_mins, c.min_bedrooms,
               c.required_subtypes_json, c.required_amenities_json,
               w.affordability_w, w.commute_w, w.type_fit_w, w.space_w,
               w.amenities_w, w.proximity_w, w.quality_w
        FROM profiles p
        LEFT JOIN profile_constraints c ON c.profile_id = p.id
        LEFT JOIN profile_weights w ON w.profile_id = p.id
        ORDER BY p.is_active DESC, p.name ASC
        """
    )
    rows = await cur.fetchall()
    profile_ids = [r["id"] for r in rows]
    commute_scenarios_by_profile = await list_profile_commute_scenarios_for_profiles(db, profile_ids)
    out: list[dict[str, Any]] = []
    for r in rows:
        profile_id = r["id"]
        out.append(
            {
                "id": profile_id,
                "name": r["name"],
                "is_active": bool(r["is_active"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "constraints": {
                    "max_true_monthly": r["max_true_monthly"],
                    "max_expected_commute_mins": r["max_expected_commute_mins"],
                    "min_bedrooms": r["min_bedrooms"],
                    "required_subtypes": _loads_json_list(r["required_subtypes_json"]),
                    "required_amenities": _loads_json_list(r["required_amenities_json"]),
                },
                "weights": {
                    "affordability": r["affordability_w"] or 0.0,
                    "commute": r["commute_w"] or 0.0,
                    "type_fit": r["type_fit_w"] or 0.0,
                    "space": r["space_w"] or 0.0,
                    "amenities": r["amenities_w"] or 0.0,
                    "proximity": r["proximity_w"] or 0.0,
                    "quality": r["quality_w"] or 0.0,
                },
                "commute_scenarios": commute_scenarios_by_profile.get(profile_id, []),
            }
        )
    return out


async def get_active_profile(db: aiosqlite.Connection) -> dict[str, Any] | None:
    profiles = await list_profiles(db)
    for p in profiles:
        if p["is_active"]:
            return p
    return profiles[0] if profiles else None


async def create_profile(db: aiosqlite.Connection, name: str) -> dict[str, Any]:
    await ensure_default_profile(db)
    now = datetime.utcnow().isoformat()
    cur = await db.execute(
        "INSERT INTO profiles(name, is_active, created_at, updated_at) VALUES(?,?,?,?)",
        (name.strip(), 0, now, now),
    )
    profile_id = cur.lastrowid
    await db.execute(
        """
        INSERT INTO profile_constraints(
            profile_id, max_true_monthly, max_expected_commute_mins, min_bedrooms,
            required_subtypes_json, required_amenities_json
        ) VALUES (?,?,?,?,?,?)
        """,
        (profile_id, None, None, None, json.dumps([]), json.dumps([])),
    )
    await db.execute(
        """
        INSERT INTO profile_weights(
            profile_id, affordability_w, commute_w, type_fit_w, space_w,
            amenities_w, proximity_w, quality_w
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (profile_id, 0.35, 0.20, 0.10, 0.10, 0.10, 0.10, 0.05),
    )
    await db.commit()

    profiles = await list_profiles(db)
    for p in profiles:
        if p["id"] == profile_id:
            return p
    raise RuntimeError("Profile could not be created")


async def get_profile_by_id(db: aiosqlite.Connection, profile_id: int) -> dict[str, Any] | None:
    profiles = await list_profiles(db)
    for p in profiles:
        if p["id"] == profile_id:
            p["commute_scenarios"] = await list_profile_commute_scenarios(db, profile_id)
            return p
    return None


async def set_active_profile(db: aiosqlite.Connection, profile_id: int) -> bool:
    await ensure_default_profile(db)
    cur = await db.execute("SELECT id FROM profiles WHERE id=?", (profile_id,))
    row = await cur.fetchone()
    if not row:
        return False

    now = datetime.utcnow().isoformat()
    await db.execute("UPDATE profiles SET is_active=0")
    await db.execute(
        "UPDATE profiles SET is_active=1, updated_at=? WHERE id=?",
        (now, profile_id),
    )
    await db.commit()
    return True


async def list_profile_commute_scenarios(db: aiosqlite.Connection, profile_id: int) -> list[dict[str, Any]]:
    scenarios_by_profile = await list_profile_commute_scenarios_for_profiles(db, [profile_id])
    return scenarios_by_profile.get(profile_id, [])


async def list_profile_commute_scenarios_for_profiles(
    db: aiosqlite.Connection,
    profile_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not profile_ids:
        return {}

    placeholders = ",".join("?" for _ in profile_ids)
    cur = await db.execute(
        f"""
        SELECT id, profile_id, name, lat, lon, probability
        FROM profile_commute_scenarios
        WHERE profile_id IN ({placeholders})
        ORDER BY profile_id ASC, id ASC
        """,
        tuple(profile_ids),
    )
    rows = await cur.fetchall()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        grouped[r["profile_id"]].append(
            {
                "id": r["id"],
                "profile_id": r["profile_id"],
                "name": r["name"],
                "lat": r["lat"],
                "lon": r["lon"],
                "probability": r["probability"],
            }
        )
    return dict(grouped)


async def update_profile(
    db: aiosqlite.Connection,
    profile_id: int,
    constraints: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
    commute_scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    now = datetime.utcnow().isoformat()
    cur = await db.execute("SELECT id FROM profiles WHERE id=?", (profile_id,))
    row = await cur.fetchone()
    if not row:
        return None

    if constraints is not None:
        cur = await db.execute(
            """
            SELECT max_true_monthly, max_expected_commute_mins, min_bedrooms,
                   required_subtypes_json, required_amenities_json
            FROM profile_constraints
            WHERE profile_id=?
            """,
            (profile_id,),
        )
        existing_constraints = await cur.fetchone()
        merged_constraints = {
            "max_true_monthly": (
                existing_constraints["max_true_monthly"] if existing_constraints else None
            ),
            "max_expected_commute_mins": (
                existing_constraints["max_expected_commute_mins"] if existing_constraints else None
            ),
            "min_bedrooms": existing_constraints["min_bedrooms"] if existing_constraints else None,
            "required_subtypes": (
                _loads_json_list(existing_constraints["required_subtypes_json"])
                if existing_constraints
                else []
            ),
            "required_amenities": (
                _loads_json_list(existing_constraints["required_amenities_json"])
                if existing_constraints
                else []
            ),
        }
        merged_constraints.update(constraints)
        await db.execute(
            """
            UPDATE profile_constraints
               SET max_true_monthly=?,
                   max_expected_commute_mins=?,
                   min_bedrooms=?,
                   required_subtypes_json=?,
                   required_amenities_json=?
             WHERE profile_id=?
            """,
            (
                merged_constraints["max_true_monthly"],
                merged_constraints["max_expected_commute_mins"],
                merged_constraints["min_bedrooms"],
                json.dumps(merged_constraints["required_subtypes"]),
                json.dumps(merged_constraints["required_amenities"]),
                profile_id,
            ),
        )

    if weights is not None:
        cur = await db.execute(
            """
            SELECT affordability_w, commute_w, type_fit_w, space_w,
                   amenities_w, proximity_w, quality_w
            FROM profile_weights
            WHERE profile_id=?
            """,
            (profile_id,),
        )
        existing_weights = await cur.fetchone()
        merged_weights = {
            "affordability": existing_weights["affordability_w"] if existing_weights else 0.0,
            "commute": existing_weights["commute_w"] if existing_weights else 0.0,
            "type_fit": existing_weights["type_fit_w"] if existing_weights else 0.0,
            "space": existing_weights["space_w"] if existing_weights else 0.0,
            "amenities": existing_weights["amenities_w"] if existing_weights else 0.0,
            "proximity": existing_weights["proximity_w"] if existing_weights else 0.0,
            "quality": existing_weights["quality_w"] if existing_weights else 0.0,
        }
        merged_weights.update(weights)
        await db.execute(
            """
            UPDATE profile_weights
               SET affordability_w=?,
                   commute_w=?,
                   type_fit_w=?,
                   space_w=?,
                   amenities_w=?,
                   proximity_w=?,
                   quality_w=?
             WHERE profile_id=?
            """,
            (
                merged_weights["affordability"],
                merged_weights["commute"],
                merged_weights["type_fit"],
                merged_weights["space"],
                merged_weights["amenities"],
                merged_weights["proximity"],
                merged_weights["quality"],
                profile_id,
            ),
        )

    if commute_scenarios is not None:
        await db.execute("DELETE FROM profile_commute_scenarios WHERE profile_id=?", (profile_id,))
        for item in commute_scenarios:
            await db.execute(
                """
                INSERT INTO profile_commute_scenarios(profile_id, name, lat, lon, probability)
                VALUES (?,?,?,?,?)
                """,
                (
                    profile_id,
                    item["name"],
                    item["lat"],
                    item["lon"],
                    float(item.get("probability", 1.0)),
                ),
            )
    await db.execute("UPDATE profiles SET updated_at=? WHERE id=?", (now, profile_id))
    await db.commit()
    return await get_profile_by_id(db, profile_id)


def _loads_json_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        data = json.loads(value)
    except Exception:
        return []
    if isinstance(data, list):
        return [str(v) for v in data]
    return []
