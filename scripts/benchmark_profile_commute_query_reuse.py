#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.connection import get_db
from db.locations import list_all
from db.units import get_latest_units
import db.profiles as profiles_db
from models import Apartment


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.strip().lower().split())


async def _run_old_pattern(db, apartment_ids: list[int], active_profile_id: int) -> None:
    for apt_id in apartment_ids:
        await get_latest_units(db, apt_id)
        await profiles_db.list_profile_commute_scenarios(db, active_profile_id)


async def _run_new_pattern(db, apartment_ids: list[int], active_profile_id: int) -> None:
    await profiles_db.list_profile_commute_scenarios(db, active_profile_id)
    for apt_id in apartment_ids:
        await get_latest_units(db, apt_id)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark per-apartment commute scenario loading vs request-scoped reuse "
            "for apartment scoring paths."
        )
    )
    parser.add_argument(
        "--apartments",
        type=int,
        default=120,
        help="Target number of apartment iterations (default: 120).",
    )
    args = parser.parse_args()

    async with get_db() as db:
        await profiles_db.ensure_default_profile(db)
        active_profile = await profiles_db.get_active_profile(db)
        if not active_profile:
            raise RuntimeError("No active profile available")

        all_locations = await list_all(db, None)
        apartment_ids = [loc.id for loc in all_locations if isinstance(loc, Apartment) and loc.id is not None]
        if not apartment_ids:
            raise RuntimeError("No apartments found; add apartment locations before running benchmark.")

        # Keep benchmark deterministic and >=100 iterations even with sparse data.
        target_count = max(args.apartments, 100)
        if len(apartment_ids) < target_count:
            repeats = (target_count // len(apartment_ids)) + 1
            apartment_ids = (apartment_ids * repeats)[:target_count]
        else:
            apartment_ids = apartment_ids[:target_count]

        statements: Counter[str] = Counter()

        def trace(sql: str) -> None:
            statements[_normalize_sql(sql)] += 1

        await db.set_trace_callback(trace)

        def read_counts() -> tuple[int, int]:
            commute_queries = sum(
                count
                for sql, count in statements.items()
                if "from profile_commute_scenarios" in sql
            )
            total_queries = sum(statements.values())
            return commute_queries, total_queries

        async def run_variant(name: str, runner) -> dict[str, float | int]:
            statements.clear()
            t0 = time.perf_counter()
            await runner(db, apartment_ids, active_profile["id"])
            elapsed_ms = (time.perf_counter() - t0) * 1000
            commute_queries, total_queries = read_counts()
            return {
                "name": name,
                "elapsed_ms": elapsed_ms,
                "commute_queries": commute_queries,
                "total_queries": total_queries,
            }

        old_result = await run_variant("old_loop_pattern", _run_old_pattern)
        new_result = await run_variant("new_request_scoped_pattern", _run_new_pattern)
        await db.set_trace_callback(None)

    reduction = old_result["commute_queries"] - new_result["commute_queries"]
    ratio = (
        old_result["commute_queries"] / new_result["commute_queries"]
        if new_result["commute_queries"]
        else float("inf")
    )

    print(f"Apartments iterated: {len(apartment_ids)}")
    print(
        f"{old_result['name']}: {old_result['commute_queries']} commute queries, "
        f"{old_result['total_queries']} total queries, {old_result['elapsed_ms']:.2f} ms"
    )
    print(
        f"{new_result['name']}: {new_result['commute_queries']} commute queries, "
        f"{new_result['total_queries']} total queries, {new_result['elapsed_ms']:.2f} ms"
    )
    print(f"Commute-query reduction: {reduction} ({ratio:.1f}x fewer in new pattern)")


if __name__ == "__main__":
    asyncio.run(main())
