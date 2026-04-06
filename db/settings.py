from __future__ import annotations

import json

import aiosqlite


async def get_setting(db: aiosqlite.Connection, key: str, default=None):
    cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return row[0]


async def set_setting(db: aiosqlite.Connection, key: str, value) -> None:
    await db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )
    await db.commit()


async def delete_setting(db: aiosqlite.Connection, key: str) -> None:
    await db.execute("DELETE FROM settings WHERE key=?", (key,))
    await db.commit()
