"""Orchestrates all scrapers, detects changes, and writes history."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from db.connection import get_db
from db.locations import mark_scraped, insert
from db.units import upsert_units, get_latest_units, get_previous_units
from db.history import log_change
from models import Apartment, Gym, Hospital, Location, LocationType, Unit

logger = logging.getLogger(__name__)
console = Console()

# Max concurrent scrapers (be polite to servers)
_MAX_CONCURRENT = 2


class ScrapeRunner:
    async def run_all(self, locations: list[Location]) -> None:
        sem = asyncio.Semaphore(_MAX_CONCURRENT)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Scraping locations…", total=len(locations))

            async def scrape_one(loc: Location) -> None:
                async with sem:
                    await self._scrape_location(loc)
                    progress.advance(task)

            await asyncio.gather(*(scrape_one(loc) for loc in locations))

        console.print("[bold green]✓[/] Scrape complete.")

    async def _scrape_location(self, loc: Location) -> None:
        console.print(f"  → [dim]{loc.name}[/]")
        try:
            if isinstance(loc, Apartment):
                await self._scrape_apartment(loc)
            elif isinstance(loc, (Gym, Hospital)):
                await self._scrape_places(loc)
            else:
                logger.warning(f"No scraper for type {loc.location_type}")
        except Exception as e:
            logger.error(f"Runner error for {loc.name}: {e}")
            async with get_db() as db:
                await mark_scraped(db, loc.id, success=False, error_msg=str(e))

    async def _scrape_apartment(self, apartment: Apartment) -> None:
        from scrapers.apartments_com import ApartmentsComScraper
        from scrapers.property_sites import PropertySiteScraper

        scraper: ApartmentsComScraper | PropertySiteScraper

        # Prefer apartments.com scraper if we have a slug
        if apartment.apartments_com_slug:
            scraper = ApartmentsComScraper()
        else:
            scraper = PropertySiteScraper()

        try:
            updated = await scraper.scrape(apartment)
        finally:
            await scraper.close()

        async with get_db() as db:
            if updated.units:
                # Load previous units for change detection
                prev_units = await get_previous_units(db, apartment.id)
                await upsert_units(db, apartment.id, updated.units)
                await _detect_unit_changes(db, apartment.id, prev_units, updated.units)
                await mark_scraped(db, apartment.id, success=True)

                # Update rating if changed
                await _check_field_change(
                    db, apartment.id, "rating",
                    str(apartment.rating), str(updated.rating),
                )
                # Re-insert to update rating/review_count
                await insert(db, updated)
            else:
                await mark_scraped(db, apartment.id, success=False, error_msg="No units extracted")

    async def _scrape_places(self, loc: Location) -> None:
        from scrapers.google_places import GooglePlacesScraper
        try:
            gp = GooglePlacesScraper()
        except ValueError as e:
            console.print(f"  [yellow]⚠ {e} — skipping Google Places scrape[/]")
            return

        updated = await gp.search_and_update(loc)

        async with get_db() as db:
            await _check_field_change(
                db, loc.id, "rating",
                str(loc.rating), str(updated.rating),
            )
            await insert(db, updated)
            await mark_scraped(db, loc.id, success=True)


async def _detect_unit_changes(
    db,
    location_id: int,
    prev_units: list[Unit],
    new_units: list[Unit],
) -> None:
    """Detect price/availability changes and write them to change_log."""
    prev_map = {_unit_key(u): u for u in prev_units}
    new_map = {_unit_key(u): u for u in new_units}

    # Check existing units for changes
    for key, new_u in new_map.items():
        prev_u = prev_map.get(key)
        if not prev_u:
            # New unit appeared
            await log_change(db, location_id, f"unit.new[{key}]", None, new_u.price_display)
            continue
        if prev_u.price_min != new_u.price_min:
            await log_change(
                db, location_id, f"unit.price_min[{key}]",
                str(prev_u.price_min), str(new_u.price_min),
            )
        if prev_u.available != new_u.available:
            await log_change(
                db, location_id, f"unit.available[{key}]",
                str(prev_u.available), str(new_u.available),
            )

    # Units that disappeared
    for key in prev_map:
        if key not in new_map:
            await log_change(db, location_id, f"unit.gone[{key}]", prev_map[key].price_display, None)


async def _check_field_change(
    db,
    location_id: int,
    field: str,
    old_val: str | None,
    new_val: str | None,
) -> None:
    if old_val and new_val and old_val != new_val and new_val != "None":
        await log_change(db, location_id, field, old_val, new_val)


def _unit_key(u: Unit) -> str:
    return f"{u.floor_plan_name}|{u.bed}bd|{u.bath}ba"
