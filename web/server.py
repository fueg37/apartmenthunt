"""FastAPI web dashboard server."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from db.connection import get_db
from db.locations import list_all, get_by_id
from db.units import get_latest_units
from db.history import get_changes
from models import Apartment, LocationType

_HERE = Path(__file__).parent

app = FastAPI(title="Apartment Hunt Dashboard")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def _format_price_range(prices: list[int | None]) -> str | None:
    priced_values = [price for price in prices if price is not None]
    if not priced_values:
        return None

    lo, hi = min(priced_values), max(priced_values)
    if lo == hi:
        return f"${lo:,}/mo"
    return f"${lo:,} – ${hi:,}/mo"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/locations")
async def api_locations(
    type: str | None = Query(None),
    available: bool = Query(False),
    max_price: int | None = Query(None),
) -> JSONResponse:
    lt = None
    if type:
        try:
            lt = LocationType(type)
        except ValueError:
            raise HTTPException(400, f"Unknown type '{type}'")

    async with get_db() as db:
        locations = await list_all(db, lt)
        result = []
        for loc in locations:
            d = _loc_to_dict(loc)
            if isinstance(loc, Apartment):
                units = await get_latest_units(db, loc.id)
                unit_price_mins = [u.price_min for u in units]
                d["units"] = [_unit_to_dict(u) for u in units]
                d["available_count"] = sum(1 for u in units if u.available)
                d["price_range"] = _format_price_range(unit_price_mins)
                if available and not any(u.available for u in units):
                    continue
                if max_price is not None:
                    prices = [price for price in unit_price_mins if price is not None]
                    if prices and min(prices) > max_price:
                        continue
            result.append(d)

    return JSONResponse(result)


@app.get("/api/locations/{loc_id}")
async def api_location_detail(loc_id: int) -> JSONResponse:
    async with get_db() as db:
        loc = await get_by_id(db, loc_id)
        if not loc:
            raise HTTPException(404, "Location not found")
        d = _loc_to_dict(loc)
        if isinstance(loc, Apartment):
            units = await get_latest_units(db, loc.id)
            d["units"] = [_unit_to_dict(u) for u in units]
            d["available_count"] = sum(1 for u in units if u.available)
    return JSONResponse(d)


@app.get("/api/changes")
async def api_changes(
    since_hours: int = Query(24, ge=1, le=720),
    location_id: int | None = Query(None),
) -> JSONResponse:
    since = datetime.utcnow() - timedelta(hours=since_hours)
    async with get_db() as db:
        changes = await get_changes(db, since=since, location_id=location_id)
    return JSONResponse(changes)


@app.post("/api/scrape")
async def api_scrape_trigger() -> JSONResponse:
    """Trigger a background scrape of all locations."""
    import asyncio
    from scrapers.runner import ScrapeRunner

    async def _run():
        async with get_db() as db:
            locations = await list_all(db)
        runner = ScrapeRunner()
        await runner.run_all(locations)

    asyncio.create_task(_run())
    return JSONResponse({"status": "scrape started"})


@app.get("/api/search")
async def api_search(
    area: str | None = Query(None),
    max_price: int = Query(3500),
    min_beds: int = Query(2),
) -> JSONResponse:
    """Live search apartments.com and return results (not stored)."""
    from scrapers.apartments_com import ApartmentsComScraper
    from config import SEARCH_BBOX

    scraper = ApartmentsComScraper()
    try:
        results = await scraper.search(
            bbox=SEARCH_BBOX,
            min_price=1800,
            max_price=max_price,
            min_beds=min_beds,
        )
    except Exception as e:
        raise HTTPException(500, f"Search failed: {e}")
    finally:
        await scraper.close()

    return JSONResponse([_loc_to_dict(r) for r in results])


def _loc_to_dict(loc) -> dict[str, Any]:
    last_scraped = loc.last_scraped.isoformat() if loc.last_scraped else None
    return {
        "id": loc.id,
        "name": loc.name,
        "address": loc.address,
        "lat": loc.lat,
        "lon": loc.lon,
        "location_type": loc.location_type.value,
        "rating": loc.rating,
        "review_count": loc.review_count,
        "website_url": loc.website_url,
        "phone": loc.phone,
        "is_top_pick": loc.is_top_pick,
        "notes": loc.notes,
        "last_scraped": last_scraped,
        "scraped_ok": loc.scraped_ok,
    }


def _unit_to_dict(u) -> dict[str, Any]:
    return {
        "floor_plan_name": u.floor_plan_name,
        "bed": u.bed,
        "bath": u.bath,
        "sqft_min": u.sqft_min,
        "sqft_max": u.sqft_max,
        "price_min": u.price_min,
        "price_max": u.price_max,
        "available": u.available,
        "move_in_date": u.move_in_date.isoformat() if u.move_in_date else None,
        "unit_number": u.unit_number,
        "price_display": u.price_display,
        "size_display": u.size_display,
    }
