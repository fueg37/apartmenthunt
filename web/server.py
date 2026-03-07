"""FastAPI web dashboard server."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from db.connection import get_db
from db.locations import list_all, get_by_id, get_by_name, insert
from db.units import get_latest_units
from db.history import get_changes
from models import Apartment, Gym, Hospital, LocationType, PointOfInterest

_HERE = Path(__file__).parent

app = FastAPI(title="Apartment Hunt Dashboard")
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))


class LocationCreateRequest(BaseModel):
    name: str
    address: str
    lat: float
    lon: float
    location_type: str
    website_url: str | None = None
    phone: str | None = None
    is_top_pick: bool = False
    notes: str | None = None

    apartments_com_slug: str | None = None
    google_place_id: str | None = None
    hours: str | None = None
    equipment_highlights: list[str] = Field(default_factory=list)
    health_system: str | None = None
    category: str | None = None


class UrlEnrichmentRequest(BaseModel):
    url: str


class UrlEnrichmentResponse(BaseModel):
    url: str
    normalized_url: str
    inferred_type: str
    suggested_name: str | None = None
    suggested_address: str | None = None
    lat: float | None = None
    lon: float | None = None
    apartments_com_slug: str | None = None
    website_url: str | None = None
    google_place_id: str | None = None



def _format_price_range(prices: list[int | None]) -> str | None:
    priced_values = [price for price in prices if price is not None]
    if not priced_values:
        return None

    lo, hi = min(priced_values), max(priced_values)
    if lo == hi:
        return f"${lo:,}/mo"
    return f"${lo:,} – ${hi:,}/mo"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _decision_insight_for_location(loc, units: list | None = None) -> dict[str, Any]:
    """Compute an opinionated decision score and transparent component breakdown."""
    components: dict[str, float] = {}
    reasons: list[str] = []

    rating = loc.rating or 0.0
    rating_component = _clamp((rating / 5.0) * 100.0)
    components["rating"] = rating_component
    if loc.rating:
        reasons.append(f"Rated {loc.rating:.1f}★ by users")

    if loc.last_scraped:
        age_hours = max((datetime.utcnow() - loc.last_scraped).total_seconds() / 3600.0, 0.0)
        freshness_component = _clamp(100.0 - (age_hours * 2.5))
    else:
        freshness_component = 35.0
    components["freshness"] = freshness_component

    top_pick_component = 100.0 if loc.is_top_pick else 0.0
    components["priority_signal"] = top_pick_component
    if loc.is_top_pick:
        reasons.append("Marked as a top pick")

    if isinstance(loc, Apartment):
        unit_list = units or []
        priced = [u.price_min for u in unit_list if u.price_min is not None]
        if priced:
            cheapest = min(priced)
            target_budget = 3500
            affordability_component = _clamp(100.0 - ((cheapest - 1800) / (target_budget - 1800)) * 100.0)
            components["affordability"] = affordability_component
            reasons.append(f"Lowest known rent starts at ${cheapest:,}/mo")
        else:
            components["affordability"] = 40.0
            reasons.append("No recent unit pricing detected")

        available_count = sum(1 for u in unit_list if u.available)
        availability_component = _clamp(available_count * 25.0)
        components["availability"] = availability_component
        if available_count:
            reasons.append(f"{available_count} unit{'s' if available_count != 1 else ''} currently available")
        else:
            reasons.append("No units currently flagged available")

        weights = {
            "affordability": 0.32,
            "availability": 0.28,
            "rating": 0.20,
            "freshness": 0.12,
            "priority_signal": 0.08,
        }
    else:
        if isinstance(loc, Gym):
            type_focus = 74.0
        elif isinstance(loc, Hospital):
            type_focus = 76.0
        else:
            type_focus = 68.0
        components["type_fit"] = type_focus

        weights = {
            "type_fit": 0.45,
            "rating": 0.30,
            "freshness": 0.18,
            "priority_signal": 0.07,
        }

    weighted_score = sum(components[key] * weight for key, weight in weights.items())
    score = int(round(_clamp(weighted_score)))

    if score >= 80:
        tier = "strong_fit"
        summary = "Strong fit right now"
    elif score >= 65:
        tier = "promising"
        summary = "Promising, worth active monitoring"
    elif score >= 50:
        tier = "watch"
        summary = "Watch list candidate"
    else:
        tier = "speculative"
        summary = "Speculative; needs better signals"

    return {
        "score": score,
        "tier": tier,
        "summary": summary,
        "components": {k: int(round(v)) for k, v in components.items()},
        "reasons": reasons[:3],
        "version": "v1",
    }


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
                d["decision_insight"] = _decision_insight_for_location(loc, units)
                if available and not any(u.available for u in units):
                    continue
                if max_price is not None:
                    prices = [price for price in unit_price_mins if price is not None]
                    if prices and min(prices) > max_price:
                        continue
            else:
                d["decision_insight"] = _decision_insight_for_location(loc)
            result.append(d)

    return JSONResponse(result)




@app.post("/api/locations")
async def api_add_location(payload: LocationCreateRequest) -> JSONResponse:
    from commands.add import _build_location

    try:
        loc = _build_location(
            name=payload.name.strip(),
            address=payload.address.strip(),
            lat=payload.lat,
            lon=payload.lon,
            location_type_str=payload.location_type.strip(),
            website_url=(payload.website_url.strip() if payload.website_url else None),
            is_top_pick=payload.is_top_pick,
            notes=(payload.notes.strip() if payload.notes else None),
            apartments_com_slug=(payload.apartments_com_slug.strip() if payload.apartments_com_slug else None),
            google_place_id=(payload.google_place_id.strip() if payload.google_place_id else None),
            hours=(payload.hours.strip() if payload.hours else None),
            equipment_highlights=[h.strip() for h in payload.equipment_highlights if h.strip()],
            health_system=(payload.health_system.strip() if payload.health_system else None),
            category=(payload.category.strip() if payload.category else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.phone:
        loc.phone = payload.phone.strip()

    async with get_db() as db:
        await insert(db, loc)
        stored = await get_by_name(db, loc.name)

    if not stored:
        raise HTTPException(status_code=500, detail="Location could not be saved")

    return JSONResponse(_loc_to_dict(stored), status_code=201)


@app.post("/api/locations/enrich-from-url")
async def api_enrich_from_url(payload: UrlEnrichmentRequest) -> JSONResponse:
    try:
        enriched = _enrich_from_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(UrlEnrichmentResponse(**enriched).model_dump())

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
            d["decision_insight"] = _decision_insight_for_location(loc, units)
        else:
            d["decision_insight"] = _decision_insight_for_location(loc)
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
    data = {
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
    if isinstance(loc, Apartment):
        data["apartments_com_slug"] = loc.apartments_com_slug
    if isinstance(loc, Gym):
        data["google_place_id"] = loc.google_place_id
        data["hours"] = loc.hours
        data["equipment_highlights"] = loc.equipment_highlights
    if isinstance(loc, Hospital):
        data["google_place_id"] = loc.google_place_id
        data["hours"] = loc.hours
        data["health_system"] = loc.health_system
    if isinstance(loc, PointOfInterest):
        data["category"] = loc.category
    return data


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


def _enrich_from_url(raw_url: str) -> dict[str, Any]:
    if not raw_url or not raw_url.strip():
        raise ValueError("URL is required")

    normalized = raw_url.strip()
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path or ""

    inferred_type = _infer_type_from_url(host, path, parsed.query)
    apartments_slug = _extract_apartments_slug(host, path)
    google_place_id = _extract_google_place_id(parsed)
    lat, lon = _extract_lat_lon(parsed)
    suggested_name = _extract_name_hint(parsed)

    return {
        "url": raw_url,
        "normalized_url": normalized,
        "inferred_type": inferred_type,
        "suggested_name": suggested_name,
        "suggested_address": None,
        "lat": lat,
        "lon": lon,
        "apartments_com_slug": apartments_slug,
        "website_url": normalized,
        "google_place_id": google_place_id,
    }


def _infer_type_from_url(host: str, path: str, query: str = "") -> str:
    combined = f"{host}{path}?{query}".lower()
    if "apartments.com" in host:
        return "apartment"
    if any(k in combined for k in ("hospital", "medical-center", "health")):
        return "hospital"
    if any(k in combined for k in ("gym", "fitness", "crossfit", "workout")):
        return "gym"
    is_google_maps = "maps.google" in host or host.endswith("goo.gl") or ("google." in host and "/maps" in path)
    if is_google_maps:
        if any(k in combined for k in ("hospital", "medical", "health")):
            return "hospital"
        if any(k in combined for k in ("gym", "fitness", "crossfit")):
            return "gym"
        return "poi"
    return "poi"


def _extract_apartments_slug(host: str, path: str) -> str | None:
    if "apartments.com" not in host:
        return None
    slug = path.strip("/").split("/")[0]
    if slug and slug not in {"", "apartments"}:
        return slug
    return None


def _extract_google_place_id(parsed) -> str | None:
    q = parse_qs(parsed.query)
    for key in ("place_id", "q", "query"):
        vals = q.get(key)
        if not vals:
            continue
        val = vals[0]
        match = re.search(r"(ChI[A-Za-z0-9_-]+)", val)
        if match:
            return match.group(1)
    return None


def _extract_lat_lon(parsed) -> tuple[float | None, float | None]:
    text = f"{parsed.path} {parsed.query}"
    match = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", text)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def _extract_name_hint(parsed) -> str | None:
    q = parse_qs(parsed.query)
    for key in ("q", "query"):
        vals = q.get(key)
        if vals:
            candidate = vals[0]
            if "ChI" in candidate:
                continue
            candidate = unquote(candidate).replace("+", " ").strip()
            if candidate:
                return candidate
    return None
