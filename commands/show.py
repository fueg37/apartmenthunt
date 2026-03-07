"""hunt show — display tracked locations with optional filters."""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box

from db.connection import get_db
from db.locations import list_all
from db.units import get_latest_units
from models import Apartment, LocationType

console = Console()

app = typer.Typer(help="Display tracked locations.")


def run(
    location_type: Optional[str] = None,
    max_price: Optional[int] = None,
    available_only: bool = False,
    top_picks_only: bool = False,
    beds: Optional[int] = None,
) -> None:
    asyncio.run(_show(location_type, max_price, available_only, top_picks_only, beds))


async def _show(
    location_type_str: str | None,
    max_price: int | None,
    available_only: bool,
    top_picks_only: bool,
    beds: int | None,
) -> None:
    lt = None
    if location_type_str:
        try:
            lt = LocationType(location_type_str.lower())
        except ValueError:
            console.print(f"[red]Unknown type '{location_type_str}'. Use: apartment, gym, hospital, poi[/]")
            raise typer.Exit(1)

    async with get_db() as db:
        locations = await list_all(db, lt)

        if top_picks_only:
            locations = [l for l in locations if l.is_top_pick]

        # Load units for apartments
        for loc in locations:
            if isinstance(loc, Apartment):
                loc.units = await get_latest_units(db, loc.id)

        # Apply price filter
        if max_price is not None:
            def passes_price(loc) -> bool:
                if not isinstance(loc, Apartment):
                    return True
                if not loc.units:
                    return True  # no data yet, include
                return any(u.price_min and u.price_min <= max_price for u in loc.units)
            locations = [l for l in locations if passes_price(l)]

        # Apply bedroom filter
        if beds is not None:
            for loc in locations:
                if isinstance(loc, Apartment):
                    loc.units = [u for u in loc.units if u.bed == beds]

        # Apply availability filter
        if available_only:
            def has_available(loc) -> bool:
                if not isinstance(loc, Apartment):
                    return True
                return bool(loc.available_units)
            locations = [l for l in locations if has_available(l)]

        if not locations:
            console.print("[yellow]No locations match your filters.[/]")
            return

        _render_table(locations, show_units=lt == LocationType.APARTMENT or lt is None)


def _render_table(locations, show_units: bool) -> None:
    # Group by type
    by_type: dict[LocationType, list] = {}
    for loc in locations:
        by_type.setdefault(loc.location_type, []).append(loc)

    type_colors = {
        LocationType.APARTMENT: "steel_blue1",
        LocationType.GYM: "green3",
        LocationType.HOSPITAL: "salmon1",
        LocationType.POI: "plum3",
    }
    type_labels = {
        LocationType.APARTMENT: "Apartments",
        LocationType.GYM: "Gyms",
        LocationType.HOSPITAL: "Hospitals",
        LocationType.POI: "Points of Interest",
    }

    for lt, locs in by_type.items():
        color = type_colors[lt]
        label = type_labels[lt]

        table = Table(
            title=f"[bold {color}]{label}[/]",
            box=box.ROUNDED,
            show_lines=True,
            title_justify="left",
        )
        table.add_column("Name", style="bold", min_width=24)
        table.add_column("Area", min_width=12)
        table.add_column("Rating", justify="center", min_width=12)
        table.add_column("Pick", justify="center", min_width=5)

        if lt == LocationType.APARTMENT and show_units:
            table.add_column("Price Range", min_width=20)
            table.add_column("Available Units", min_width=14, justify="right")
            table.add_column("Last Scraped", min_width=16)

        for loc in locs:
            area = _area_from_address(loc.address)
            pick = "⭐ TOP" if loc.is_top_pick else ""
            base_row = [loc.name, area, loc.rating_display, pick]

            if lt == LocationType.APARTMENT and show_units and isinstance(loc, Apartment):
                price = loc.price_range_display if loc.units else "[dim]not scraped[/]"
                avail = str(len(loc.available_units)) if loc.units else "[dim]–[/]"
                scraped = (
                    loc.last_scraped.strftime("%m/%d %H:%M") if loc.last_scraped else "[dim]never[/]"
                )
                table.add_row(*base_row, price, avail, scraped)
            else:
                table.add_row(*base_row)

        console.print(table)
        console.print()


def _area_from_address(address: str) -> str:
    parts = address.split(",")
    if len(parts) >= 2:
        return parts[1].strip()
    return address
