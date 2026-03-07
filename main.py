"""
hunt — Palm Beach Apartment Hunt CLI

Commands:
  seed      Seed the DB with known apartments, gyms, and hospitals
  show      Display tracked locations (with optional filters)
  add       Add a new location to track
  add-file  Bulk import locations from CSV/JSON
  scrape    Scrape live data for tracked locations
  search    Find new apartments via apartments.com
  diff      Show what changed since the last scrape
  web       Start the local web dashboard
"""
from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(
    name="hunt",
    help="Palm Beach apartment hunt — live scraper & dashboard.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def seed() -> None:
    """Seed the database with known apartments, gyms, and hospitals."""
    from seed_data import seed_db
    n = asyncio.run(seed_db())
    console.print(f"[green]✓[/] Seeded [bold]{n}[/] locations into the database.")


@app.command()
def show(
    loc_type: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by type: apartment, gym, hospital, poi"),
    max_price: Optional[int] = typer.Option(None, "--max-price", "-p", help="Max monthly price (apartments only)"),
    available: bool = typer.Option(False, "--available", "-a", help="Only show locations with available units"),
    top_picks: bool = typer.Option(False, "--top-picks", help="Only show top picks"),
    beds: Optional[int] = typer.Option(None, "--beds", "-b", help="Filter apartments to N-bedroom units only"),
) -> None:
    """Display tracked locations with optional filters."""
    from commands.show import run
    run(loc_type, max_price, available, top_picks, beds)


@app.command()
def add(
    name: str = typer.Option(..., "--name", "-n", help="Location name"),
    address: str = typer.Option(..., "--address", help="Full street address"),
    lat: float = typer.Option(..., "--lat", help="Latitude"),
    lon: float = typer.Option(..., "--lon", help="Longitude"),
    loc_type: str = typer.Option(..., "--type", "-t", help="Type: apartment, gym, hospital, poi"),
    website: Optional[str] = typer.Option(None, "--website", help="Website URL"),
    top_pick: bool = typer.Option(False, "--top-pick", help="Mark as a top pick"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Personal notes"),
    apartments_com_slug: Optional[str] = typer.Option(None, "--apartments-com-slug", help="apartments.com slug for apartments"),
    google_place_id: Optional[str] = typer.Option(None, "--google-place-id", help="Google Place ID for gyms/hospitals"),
    hours: Optional[str] = typer.Option(None, "--hours", help="Operating hours for gyms/hospitals"),
    equipment_highlight: list[str] = typer.Option([], "--equipment-highlight", help="Gym equipment highlight (repeat flag)"),
    health_system: Optional[str] = typer.Option(None, "--health-system", help="Health system for hospitals"),
    category: Optional[str] = typer.Option(None, "--category", help="Custom category for points of interest"),
) -> None:
    """Manually add a new location to track."""
    from commands.add import run
    run(
        name,
        address,
        lat,
        lon,
        loc_type,
        website,
        top_pick,
        notes,
        apartments_com_slug,
        google_place_id,
        hours,
        equipment_highlight,
        health_system,
        category,
    )


@app.command("add-file")
def add_file(
    path: str = typer.Option(..., "--path", "-p", help="Path to .csv or .json file of location records"),
) -> None:
    """Bulk import locations from a CSV/JSON file."""
    from commands.add import import_file
    import_file(path)


@app.command()
def scrape(
    location: Optional[str] = typer.Option(None, "--location", "-l", help="Scrape only this location (by name)"),
) -> None:
    """Scrape live data for all (or one) tracked locations."""
    from commands.scrape import run
    run(location)


@app.command()
def search(
    area: Optional[str] = typer.Option(None, "--area", help="Area to focus on: delray, boynton, boca, lake_worth"),
    max_price: int = typer.Option(3500, "--max-price", "-p", help="Max monthly rent"),
    min_beds: int = typer.Option(2, "--min-beds", "-b", help="Minimum bedrooms"),
    add_new: bool = typer.Option(False, "--add", help="Prompt to add new results to tracking"),
) -> None:
    """Find new apartments via apartments.com search."""
    from commands.search import run
    run(area, max_price, min_beds, add_new)


@app.command()
def diff(
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Show changes since date (YYYY-MM-DD). Defaults to last 24h."),
) -> None:
    """Show what changed since the last scrape."""
    from commands.diff import run
    run(since)


@app.command()
def web(
    port: int = typer.Option(8000, "--port", "-p", help="Port for the web dashboard"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
) -> None:
    """Start the local web dashboard at http://localhost:8000."""
    import uvicorn
    console.print(f"[bold]Starting dashboard[/] at [link]http://{host}:{port}[/link]")
    uvicorn.run("web.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
