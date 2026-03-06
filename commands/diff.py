"""hunt diff — show what changed since the last scrape (or a given date)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich import box

from db.connection import get_db
from db.history import get_changes

console = Console()


def run(since: Optional[str] = None) -> None:
    asyncio.run(_diff(since))


async def _diff(since_str: str | None) -> None:
    since: datetime | None = None
    if since_str:
        try:
            since = datetime.fromisoformat(since_str)
        except ValueError:
            console.print(f"[red]Invalid date '{since_str}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS[/]")
            raise typer.Exit(1)
    else:
        # Default: last 24 hours
        since = datetime.utcnow() - timedelta(hours=24)

    async with get_db() as db:
        changes = await get_changes(db, since=since)

    if not changes:
        label = since.strftime("%Y-%m-%d %H:%M") if since else "ever"
        console.print(f"[dim]No changes recorded since {label}.[/]")
        return

    table = Table(
        title=f"[bold]Changes since {since.strftime('%Y-%m-%d %H:%M')} UTC[/]",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Location", style="bold", min_width=24)
    table.add_column("Field", min_width=20)
    table.add_column("Old Value", min_width=16, style="red")
    table.add_column("New Value", min_width=16, style="green")
    table.add_column("When (UTC)", min_width=16)

    for ch in changes:
        when = datetime.fromisoformat(ch["changed_at"]).strftime("%m/%d %H:%M")
        table.add_row(
            ch["location_name"],
            ch["field"],
            ch["old_value"] or "–",
            ch["new_value"] or "–",
            when,
        )

    console.print(table)
