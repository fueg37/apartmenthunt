"""hunt search — find new apartments via apartments.com, or gyms via Google Places."""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box
from rich.prompt import Confirm

from db.connection import get_db
from db.locations import get_by_name, insert
from models import Apartment, Gym

console = Console()

# Gym queries for powerlifting/bodybuilding discovery
_GYM_QUERIES = [
    "powerlifting gym",
    "barbell gym",
    "bodybuilding gym",
    "strength training gym",
]

# Chain/cardio gym names to filter out (lowercase tokens)
_CHAIN_GYM_NAMES = {
    "planet fitness", "la fitness", "anytime fitness", "crunch fitness",
    "crunch", "ymca", "blink fitness",
    "orange theory", "orangetheory", "f45", "pure barre", "barry's",
    "crossfit", "hyrox",
}


def run(
    location_type: str = "apartment",
    area: Optional[str] = None,
    max_price: int = 3500,
    min_beds: int = 2,
    add_new: bool = False,
    gym_query: Optional[str] = None,
    min_rating: float = 4.0,
) -> None:
    if location_type == "gym":
        asyncio.run(_search_gyms(add_new, gym_query, min_rating))
    else:
        asyncio.run(_search_apartments(area, max_price, min_beds, add_new))


async def _search_apartments(
    area: str | None,
    max_price: int,
    min_beds: int,
    add_new: bool,
) -> None:
    from scrapers.apartments_com import ApartmentsComScraper
    from config import AREA_CENTERS, SEARCH_BBOX

    console.print(f"[bold]Searching apartments.com[/] (≤${max_price:,}/mo, ≥{min_beds}BR)…")

    scraper = ApartmentsComScraper()
    try:
        results = await scraper.search(
            bbox=SEARCH_BBOX,
            min_price=1800,
            max_price=max_price,
            min_beds=min_beds,
        )
    except Exception as e:
        console.print(f"[red]Search failed:[/] {e}")
        raise typer.Exit(1)
    finally:
        await scraper.close()

    if not results:
        console.print("[yellow]No results returned.[/]")
        return

    # Filter by area if requested
    if area:
        import math
        center = AREA_CENTERS.get(area.lower())
        if not center:
            console.print(f"[red]Unknown area '{area}'. Use: delray, boynton, boca, lake_worth[/]")
            raise typer.Exit(1)
        clat, clon = center

        def nearby(apt: Apartment, radius_km: float = 8.0) -> bool:
            R = 6371.0
            dlat = math.radians(apt.lat - clat)
            dlon = math.radians(apt.lon - clon)
            a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(clat)) * math.cos(math.radians(apt.lat)) * math.sin(dlon / 2) ** 2
            return R * 2 * math.asin(math.sqrt(a)) <= radius_km

        results = [r for r in results if nearby(r)]

    console.print(f"Found [bold]{len(results)}[/] apartment(s).\n")

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("#", justify="right", min_width=3)
    table.add_column("Name", style="bold", min_width=28)
    table.add_column("Address", min_width=30)
    table.add_column("Price", min_width=18)
    table.add_column("Rating", justify="center", min_width=12)

    for i, apt in enumerate(results, 1):
        table.add_row(
            str(i),
            apt.name,
            apt.address,
            apt.price_range_display,
            apt.rating_display,
        )

    console.print(table)

    if add_new:
        async with get_db() as db:
            for apt in results:
                existing = await get_by_name(db, apt.name)
                if not existing:
                    if Confirm.ask(f"\nAdd '[bold]{apt.name}[/]' to tracking?"):
                        await insert(db, apt)
                        console.print(f"[green]✓[/] Added {apt.name}")
                else:
                    console.print(f"[dim]Already tracking: {apt.name}[/]")


async def _search_gyms(
    add_new: bool,
    custom_query: str | None,
    min_rating: float,
) -> None:
    from scrapers.google_places import GooglePlacesScraper
    from config import SEARCH_BBOX

    queries = [custom_query] if custom_query else _GYM_QUERIES
    console.print(f"[bold]Searching for powerlifting/bodybuilding gyms[/] (rating ≥ {min_rating})…")

    try:
        scraper = GooglePlacesScraper()
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)

    seen_place_ids: set[str] = set()
    gyms: list[Gym] = []

    for query in queries:
        console.print(f"  Query: [dim]{query}[/]")
        found = await scraper.search_gyms(query, max_results=10)
        for gym in found:
            place_id = gym.extra.get("google_place_id", "")
            if place_id and place_id in seen_place_ids:
                continue
            if place_id:
                seen_place_ids.add(place_id)

            # Filter to bounding box
            if not (SEARCH_BBOX["south"] <= gym.lat <= SEARCH_BBOX["north"] and
                    SEARCH_BBOX["west"] <= gym.lon <= SEARCH_BBOX["east"]):
                continue

            # Filter by rating
            if gym.rating is not None and gym.rating < min_rating:
                continue

            # Filter out chain/cardio gyms
            name_lower = gym.name.lower()
            if any(chain in name_lower for chain in _CHAIN_GYM_NAMES):
                continue

            gyms.append(gym)

    if not gyms:
        console.print("[yellow]No matching gyms found.[/]")
        return

    # Check which are already tracked
    async with get_db() as db:
        tracked_names = set()
        from db.locations import list_all
        existing = await list_all(db, location_type="gym")
        for loc in existing:
            tracked_names.add(loc.name.lower())
            if hasattr(loc, "google_place_id") and loc.google_place_id:
                tracked_names.add(loc.google_place_id)

    new_gyms = []
    already_tracked = []
    for gym in gyms:
        place_id = gym.extra.get("google_place_id", "")
        is_tracked = (
            gym.name.lower() in tracked_names or
            (place_id and place_id in tracked_names)
        )
        if is_tracked:
            already_tracked.append(gym)
        else:
            new_gyms.append(gym)

    console.print(f"Found [bold]{len(new_gyms)}[/] new gym(s) (+ {len(already_tracked)} already tracked).\n")

    if not new_gyms:
        return

    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("#", justify="right", min_width=3)
    table.add_column("Name", style="bold", min_width=28)
    table.add_column("Address", min_width=32)
    table.add_column("Rating", justify="center", min_width=12)
    table.add_column("Phone", min_width=16)

    for i, gym in enumerate(new_gyms, 1):
        table.add_row(
            str(i),
            gym.name,
            gym.address,
            f"⭐ {gym.rating:.1f} ({gym.review_count or 0})" if gym.rating else "—",
            gym.phone or "—",
        )

    console.print(table)

    if add_new:
        async with get_db() as db:
            for gym in new_gyms:
                if Confirm.ask(f"\nAdd '[bold]{gym.name}[/]' to tracking?"):
                    await insert(db, gym)
                    console.print(f"[green]✓[/] Added {gym.name}")
