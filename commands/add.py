"""hunt add — manually add one or more locations to track."""
from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import typer
from rich.console import Console

from db.connection import get_db
from db.locations import get_by_name, insert
from models import Apartment, Gym, Hospital, Location, LocationType, PointOfInterest

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
    apartments_com_slug: str | None = None,
    google_place_id: str | None = None,
    hours: str | None = None,
    equipment_highlights: list[str] | None = None,
    health_system: str | None = None,
    category: str | None = None,
    weight: float | None = None,
) -> None:
    asyncio.run(
        _add(
            name=name,
            address=address,
            lat=lat,
            lon=lon,
            location_type_str=location_type,
            website_url=website_url,
            is_top_pick=is_top_pick,
            notes=notes,
            apartments_com_slug=apartments_com_slug,
            google_place_id=google_place_id,
            hours=hours,
            equipment_highlights=equipment_highlights or [],
            health_system=health_system,
            category=category,
            weight=weight,
        )
    )


async def _add(
    name: str,
    address: str,
    lat: float,
    lon: float,
    location_type_str: str,
    website_url: str | None,
    is_top_pick: bool,
    notes: str | None,
    apartments_com_slug: str | None,
    google_place_id: str | None,
    hours: str | None,
    equipment_highlights: list[str],
    health_system: str | None,
    category: str | None,
    weight: float | None = None,
) -> None:
    loc = _build_location(
        name=name,
        address=address,
        lat=lat,
        lon=lon,
        location_type_str=location_type_str,
        website_url=website_url,
        is_top_pick=is_top_pick,
        notes=notes,
        apartments_com_slug=apartments_com_slug,
        google_place_id=google_place_id,
        hours=hours,
        equipment_highlights=equipment_highlights,
        health_system=health_system,
        category=category,
        weight=weight,
    )

    async with get_db() as db:
        existing = await get_by_name(db, name)
        if existing:
            console.print(f"[yellow]'{name}' already exists (id={existing.id}). Updating.[/]")

        loc_id = await insert(db, loc)
        console.print(
            f"[green]✓[/] Added [bold]{name}[/] (id={loc_id}) as [bold]{loc.location_type.value}[/]."
        )


def import_file(path: str) -> None:
    asyncio.run(_import_file(path))


async def _import_file(path: str) -> None:
    file_path = Path(path)
    if not file_path.exists():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(1)

    if file_path.suffix.lower() == ".json":
        items = json.loads(file_path.read_text())
    elif file_path.suffix.lower() == ".csv":
        with file_path.open(newline="", encoding="utf-8") as f:
            items = list(csv.DictReader(f))
    else:
        console.print("[red]Unsupported file type. Use .json or .csv[/]")
        raise typer.Exit(1)

    if not isinstance(items, list):
        console.print("[red]Expected a list of location records.[/]")
        raise typer.Exit(1)

    added = 0
    async with get_db() as db:
        for i, item in enumerate(items, start=1):
            try:
                loc = _build_location_from_record(item)
            except Exception as exc:
                console.print(f"[red]Row {i} skipped:[/] {exc}")
                continue

            await insert(db, loc)
            added += 1

    console.print(f"[green]✓[/] Imported [bold]{added}[/] location(s) from {file_path.name}.")


def _build_location_from_record(item: dict) -> Location:
    def _to_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _to_list(value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        raw = str(value).strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in raw.split("|") if part.strip()]

    try:
        lat = float(item["lat"])
        lon = float(item["lon"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("lat/lon are required numeric fields") from exc

    return _build_location(
        name=str(item.get("name") or "").strip(),
        address=str(item.get("address") or "").strip(),
        lat=lat,
        lon=lon,
        location_type_str=str(item.get("type") or item.get("location_type") or "").strip(),
        website_url=(str(item["website"]).strip() if item.get("website") is not None else None),
        is_top_pick=_to_bool(item.get("top_pick") if "top_pick" in item else item.get("is_top_pick")),
        notes=(str(item["notes"]).strip() if item.get("notes") is not None else None),
        apartments_com_slug=(
            str(item["apartments_com_slug"]).strip() if item.get("apartments_com_slug") is not None else None
        ),
        google_place_id=(
            str(item["google_place_id"]).strip() if item.get("google_place_id") is not None else None
        ),
        hours=(str(item["hours"]).strip() if item.get("hours") is not None else None),
        equipment_highlights=_to_list(item.get("equipment_highlights")),
        health_system=(
            str(item["health_system"]).strip() if item.get("health_system") is not None else None
        ),
        category=(str(item["category"]).strip() if item.get("category") is not None else None),
        weight=(float(item["weight"]) if item.get("weight") is not None else None),
    )


def _build_location(
    name: str,
    address: str,
    lat: float,
    lon: float,
    location_type_str: str,
    website_url: str | None,
    is_top_pick: bool,
    notes: str | None,
    apartments_com_slug: str | None,
    google_place_id: str | None,
    hours: str | None,
    equipment_highlights: list[str],
    health_system: str | None,
    category: str | None,
    weight: float | None = None,
) -> Location:
    if not name:
        raise ValueError("name is required")
    if not address:
        raise ValueError("address is required")

    try:
        lt = LocationType(location_type_str.lower())
    except ValueError as exc:
        raise ValueError(
            f"Unknown type '{location_type_str}'. Use: apartment, gym, hospital, poi"
        ) from exc

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
        return Apartment(**loc_kwargs, apartments_com_slug=apartments_com_slug)
    if lt == LocationType.GYM:
        return Gym(
            **loc_kwargs,
            google_place_id=google_place_id,
            hours=hours,
            equipment_highlights=equipment_highlights,
        )
    if lt == LocationType.HOSPITAL:
        return Hospital(
            **loc_kwargs,
            google_place_id=google_place_id,
            hours=hours,
            health_system=health_system,
        )
    return PointOfInterest(**loc_kwargs, category=category, weight=weight)
