"""hunt search — find new apartments via apartments.com."""
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
from models import Apartment

console = Console()


def run(
    area: Optional[str] = None,
    max_price: int = 3500,
    min_beds: int = 2,
    add_new: bool = False,
) -> None:
    asyncio.run(_search(area, max_price, min_beds, add_new))


async def _search(
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
        from config import AREA_CENTERS
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
