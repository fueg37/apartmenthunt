"""hunt scrape — scrape live data for all (or one) tracked locations."""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from db.connection import get_db
from db.locations import get_by_name, list_all

console = Console()


def run(location_name: Optional[str] = None, force: bool = False) -> None:
    asyncio.run(_scrape(location_name, force=force))


async def _scrape(location_name: str | None, force: bool = False) -> None:
    from scrapers.runner import ScrapeRunner

    async with get_db() as db:
        if location_name:
            loc = await get_by_name(db, location_name)
            if not loc:
                console.print(f"[red]Location '{location_name}' not found in DB.[/]")
                raise typer.Exit(1)
            locations = [loc]
        else:
            locations = await list_all(db)

    if not locations:
        console.print("[yellow]No locations in database. Run 'hunt seed' first.[/]")
        return

    runner = ScrapeRunner()
    await runner.run_all(locations, force=force)
