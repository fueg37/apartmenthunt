"""hunt add — manually add a new location to track."""
from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.prompt import Prompt, Confirm

from db.connection import get_db
from db.locations import insert, get_by_name
from models import Apartment, Gym, Hospital, LocationType

console = Console()


def run(
    name: str,
    address: str,
    lat: float,
    lon: float,
    location_type: str,
    website_url: str | None = None,
    is_top_pick: bool = False,
    notes: str | None = None,
) -> None:
    asyncio.run(_add(name, address, lat, lon, location_type, website_url, is_top_pick, notes))


async def _add(
    name: str,
    address: str,
    lat: float,
    lon: float,
    location_type_str: str,
    website_url: str | None,
    is_top_pick: bool,
    notes: str | None,
) -> None:
    try:
        lt = LocationType(location_type_str.lower())
    except ValueError:
        console.print(f"[red]Unknown type '{location_type_str}'. Use: apartment, gym, hospital[/]")
        raise typer.Exit(1)

    async with get_db() as db:
        existing = await get_by_name(db, name)
        if existing:
            console.print(f"[yellow]'{name}' already exists (id={existing.id}). Updating.[/]")

        loc_kwargs = dict(
            name=name,
            address=address,
            lat=lat,
            lon=lon,
            website_url=website_url,
            is_top_pick=is_top_pick,
            notes=notes,
        )

        if lt == LocationType.APARTMENT:
            loc = Apartment(**loc_kwargs)
        elif lt == LocationType.GYM:
            loc = Gym(**loc_kwargs)
        else:
            loc = Hospital(**loc_kwargs)

        loc_id = await insert(db, loc)
        console.print(f"[green]✓[/] Added [bold]{name}[/] (id={loc_id}) as [bold]{lt.value}[/].")
