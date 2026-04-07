"""FastAPI web dashboard server."""
from __future__ import annotations

import json
import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import FastAPI, Query, HTTPException, Body
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

from db.connection import get_db
from db.locations import list_all, get_by_id, get_by_name, insert, delete_by_id
from db.units import get_latest_units, save_manual_units, delete_manual_units
from db.history import get_changes
from db.settings import get_setting, set_setting, delete_setting
import db.discoveries as disc_db
from models import Apartment, Gym, Hospital, LocationType, PointOfInterest

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent

# Gym discovery queries and filter list (shared with CLI)
_GYM_DISCOVERY_QUERIES = [
    "powerlifting gym",
    "barbell gym",
    "bodybuilding gym",
    "strength training gym",
]
_CHAIN_GYM_NAMES = {
    "planet fitness", "la fitness", "anytime fitness", "crunch fitness",
    "crunch", "ymca", "blink fitness",
    "orange theory", "orangetheory", "f45", "pure barre", "barry's",
    "crossfit", "hyrox",
}


async def _run_gym_discovery() -> int:
    """Run gym discovery and store new finds in the discoveries inbox. Returns count added."""
    from scrapers.google_places import GooglePlacesScraper
    from config import SEARCH_BBOX, settings

    if not settings.google_places_api_key:
        logger.warning("Gym discovery skipped: GOOGLE_PLACES_API_KEY not set")
        return 0

    scraper = GooglePlacesScraper()
    seen_place_ids: set[str] = set()
    added = 0

    async with get_db() as db:
        # Get existing tracked gym place IDs to skip them
        existing_gyms = await list_all(db, location_type=LocationType.GYM)
        tracked_place_ids = {
            loc.google_place_id for loc in existing_gyms
            if hasattr(loc, "google_place_id") and loc.google_place_id
        }
        tracked_names = {loc.name.lower() for loc in existing_gyms}

        for query in _GYM_DISCOVERY_QUERIES:
            try:
                gyms = await scraper.search_gyms(query, max_results=10)
            except Exception as e:
                logger.warning(f"Gym discovery query '{query}' failed: {e}")
                continue

            for gym in gyms:
                place_id = gym.extra.get("google_place_id", "")

                if place_id and place_id in seen_place_ids:
                    continue
                if place_id:
                    seen_place_ids.add(place_id)

                # Skip if outside bounding box
                if not (SEARCH_BBOX["south"] <= gym.lat <= SEARCH_BBOX["north"] and
                        SEARCH_BBOX["west"] <= gym.lon <= SEARCH_BBOX["east"]):
                    continue

                # Skip low-rated
                if gym.rating is not None and gym.rating < 4.0:
                    continue

                # Skip chain/cardio gyms
                name_lower = gym.name.lower()
                if any(chain in name_lower for chain in _CHAIN_GYM_NAMES):
                    continue

                # Skip already tracked
                if place_id in tracked_place_ids or name_lower in tracked_names:
                    continue

                row_id = await disc_db.insert_discovery(
                    db,
                    name=gym.name,
                    address=gym.address,
                    lat=gym.lat,
                    lon=gym.lon,
                    location_type="gym",
                    source="google_places",
                    source_id=place_id or None,
                    data={
                        "rating": gym.rating,
                        "review_count": gym.review_count,
                        "phone": gym.phone,
                        "website_url": gym.website_url,
                        "google_place_id": place_id,
                        "hours": gym.hours,
                    },
                )
                if row_id:
                    added += 1

    return added


_APARTMENT_DISCOVERY_QUERIES = [
    "apartment complex",
    "luxury apartments",
    "apartments for rent",
]


async def _run_apartment_discovery() -> int:
    """Run apartment discovery via Google Places and store in inbox."""
    from scrapers.google_places import GooglePlacesScraper
    from config import SEARCH_BBOX, settings

    if not settings.google_places_api_key:
        logger.warning("Apartment discovery skipped: GOOGLE_PLACES_API_KEY not set")
        return 0

    scraper = GooglePlacesScraper()
    seen_names: set[str] = set()
    added = 0

    async with get_db() as db:
        existing = await list_all(db, location_type=LocationType.APARTMENT)
        tracked_names = {loc.name.lower() for loc in existing}

        for query in _APARTMENT_DISCOVERY_QUERIES:
            try:
                results = await scraper.search_apartments(query, max_results=10)
            except Exception as e:
                logger.warning(f"Apartment discovery query '{query}' failed: {e}")
                continue

            for apt in results:
                name_lower = apt.name.lower()
                if name_lower in seen_names or name_lower in tracked_names:
                    continue

                # Skip if outside bounding box
                if not (SEARCH_BBOX["south"] <= apt.lat <= SEARCH_BBOX["north"] and
                        SEARCH_BBOX["west"] <= apt.lon <= SEARCH_BBOX["east"]):
                    continue

                seen_names.add(name_lower)
                row_id = await disc_db.insert_discovery(
                    db,
                    name=apt.name,
                    address=apt.address,
                    lat=apt.lat,
                    lon=apt.lon,
                    location_type="apartment",
                    source="google_places",
                    source_id=apt.extra.get("google_place_id") or None,
                    data={
                        "rating": apt.rating,
                        "review_count": apt.review_count,
                        "phone": apt.phone,
                        "website_url": apt.website_url,
                    },
                )
                if row_id:
                    added += 1

    return added


async def _initial_discovery_if_empty() -> None:
    """Run discovery once on startup if inbox is empty (first run)."""
    async with get_db() as db:
        count = await disc_db.pending_count(db)
    if count == 0:
        logger.info("Discovery inbox is empty — running initial gym discovery…")
        n = await _run_gym_discovery()
        logger.info(f"Initial gym discovery added {n} candidates to inbox")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Start APScheduler for weekly background discovery
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()
        # Weekly on Sunday at 3am
        scheduler.add_job(_run_gym_discovery, "cron", day_of_week="sun", hour=3, minute=0)
        scheduler.add_job(_run_apartment_discovery, "cron", day_of_week="sun", hour=3, minute=30)
        scheduler.start()
        logger.info("Discovery scheduler started (weekly Sundays at 3am)")
    except ImportError:
        scheduler = None
        logger.info("APScheduler not installed — scheduled discovery disabled")

    # Run initial discovery if inbox is empty
    import asyncio as _asyncio
    _asyncio.create_task(_initial_discovery_if_empty())

    yield

    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Apartment Hunt Dashboard", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))


class ManualUnitInput(BaseModel):
    floor_plan_name: str
    bed: int
    bath: float
    sqft_min: int | None = None
    sqft_max: int | None = None
    price_min: int | None = None
    price_max: int | None = None


class ManualUnitsRequest(BaseModel):
    units: list[ManualUnitInput]


class AmenitiesRequest(BaseModel):
    amenities: list[str]


class ProsConsRequest(BaseModel):
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)


class VerdictRequest(BaseModel):
    verdict: str  # "up", "down", or "neutral"


class SubtypeRequest(BaseModel):
    subtype: str  # "apartment", "townhome", "condo", "studio"


class CommuteAnchorRequest(BaseModel):
    name: str
    lat: float
    lon: float


class LocationCreateRequest(BaseModel):
    name: str
    address: str
    lat: float | None = None
    lon: float | None = None
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
    weight: float | None = None
    manual_units: list[ManualUnitInput] = Field(default_factory=list)


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


def _drive_mins(lat1: float, lon1: float, lat2: float, lon2: float, mph: float = 24.0) -> int:
    """Haversine drive-time estimate in minutes at average urban speed."""
    R = 6371.0
    rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
    rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    km = R * 2 * math.asin(math.sqrt(a))
    return round(km * 0.621371 / mph * 60)


def _decision_insight_for_location(
    loc,
    units: list | None = None,
    commute_anchor: dict | None = None,
    nearby_locs: list | None = None,
) -> dict[str, Any]:
    """Compute an opinionated decision score based purely on apartment quality metrics."""
    components: dict[str, float] = {}
    base_weights: dict[str, float] = {}
    reasons: list[str] = []

    # Rating (always included)
    rating = loc.rating or 0.0
    components["rating"] = _clamp((rating / 5.0) * 100.0)
    base_weights["rating"] = 0.20
    if loc.rating:
        reasons.append(f"Rated {loc.rating:.1f}★ by users")

    # Top pick (always included)
    components["priority_signal"] = 100.0 if loc.is_top_pick else 0.0
    base_weights["priority_signal"] = 0.05
    if loc.is_top_pick:
        reasons.append("Marked as a top pick")

    # Commute (conditional — only when anchor is saved)
    if commute_anchor and loc.lat and loc.lon:
        mins = _drive_mins(loc.lat, loc.lon, commute_anchor["lat"], commute_anchor["lon"])
        # ≤10 min = 100, 30 min = 0
        components["commute"] = _clamp(100.0 - max(0.0, mins - 10) * 5.0)
        base_weights["commute"] = 0.20
        reasons.append(f"~{mins} min drive to {commute_anchor['name']}")

    # Proximity: nearest gym, grocery, or high-weight POI (conditional)
    if nearby_locs and loc.lat and loc.lon:
        gyms = [l for l in nearby_locs if isinstance(l, Gym) and l.lat and l.lon]
        priority_pois = [
            l for l in nearby_locs
            if isinstance(l, PointOfInterest) and l.lat and l.lon
            and (l.category == "grocery" or (l.weight or 0.0) >= 0.5)
        ]
        cat_scores: list[float] = []
        for group in [gyms, priority_pois]:
            if not group:
                continue
            nearest = min(_drive_mins(loc.lat, loc.lon, l.lat, l.lon) for l in group)
            # ≤5 min = 100, 15 min = 0
            cat_scores.append(_clamp(100.0 - max(0.0, nearest - 5) * 10.0))
        if cat_scores:
            components["proximity"] = sum(cat_scores) / len(cat_scores)
            base_weights["proximity"] = 0.10

    if isinstance(loc, Apartment):
        # Affordability (always included for apartments)
        unit_list = units or []
        priced = [u.price_min for u in unit_list if u.price_min is not None]
        if priced:
            cheapest = min(priced)
            target_budget = 3500
            ratio = (cheapest - 1800) / (target_budget - 1800)
            components["affordability"] = _clamp((1.0 - math.sqrt(max(0.0, ratio))) * 100.0)
            reasons.append(f"Lowest known rent starts at ${cheapest:,}/mo")
        elif unit_list:
            components["affordability"] = 50.0
            reasons.append("Pricing listed as contact-only")
        else:
            components["affordability"] = 40.0
            reasons.append("No unit pricing detected")
        base_weights["affordability"] = 0.35

        # Amenities (conditional — only if user has labeled any)
        amenities = loc.extra.get("amenities", []) if hasattr(loc, "extra") else []
        if amenities:
            components["amenities"] = _clamp((len(amenities) / 8.0) * 100.0)
            base_weights["amenities"] = 0.20
            reasons.append(f"{len(amenities)} amenit{'y' if len(amenities) == 1 else 'ies'} confirmed")

    else:
        if isinstance(loc, Gym):
            type_focus = 74.0
        elif isinstance(loc, Hospital):
            type_focus = 76.0
        else:
            type_focus = 68.0
        components["type_fit"] = type_focus
        base_weights["type_fit"] = 0.55

    # Normalise weights so they always sum to 1.0 regardless of which optional
    # components are active, then compute the weighted score.
    total_w = sum(base_weights.values())
    weights = {k: v / total_w for k, v in base_weights.items()}
    weighted_score = sum(components[k] * weights[k] for k in components)
    score = int(round(_clamp(weighted_score)))

    if score >= 80:
        tier, summary = "strong_fit", "Strong fit right now"
    elif score >= 65:
        tier, summary = "promising", "Promising — worth a visit"
    elif score >= 50:
        tier, summary = "watch", "Good but has trade-offs"
    else:
        tier = "speculative"
        worst = min(components, key=lambda k: components[k])
        summary = {
            "affordability": "Near your budget ceiling",
            "commute":       "Long commute from work anchor",
            "amenities":     "Few confirmed amenities",
            "rating":        "Low community rating",
            "proximity":     "Far from key places",
        }.get(worst, "Below threshold on key metrics")

    return {
        "score": score,
        "tier": tier,
        "summary": summary,
        "components": {k: int(round(v)) for k, v in components.items()},
        "reasons": reasons[:4],
        "version": "v3",
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
        all_locations = await list_all(db, None)  # fetch all types for scoring context
        commute_anchor = await get_setting(db, "commute_anchor")

        nearby_locs = [
            l for l in all_locations
            if isinstance(l, Gym)
            or (isinstance(l, PointOfInterest) and (
                l.category == "grocery" or (l.weight or 0.0) >= 0.5
            ))
        ]

        result = []
        for loc in all_locations:
            if lt and loc.location_type != lt:
                continue
            d = _loc_to_dict(loc)
            if isinstance(loc, Apartment):
                units = await get_latest_units(db, loc.id)
                unit_price_mins = [u.price_min for u in units]
                d["units"] = [_unit_to_dict(u) for u in units]
                d["available_count"] = sum(1 for u in units if u.available)
                d["price_range"] = _format_price_range(unit_price_mins)
                d["decision_insight"] = _decision_insight_for_location(
                    loc, units, commute_anchor, nearby_locs
                )
                if available and not any(u.available for u in units):
                    continue
                if max_price is not None:
                    prices = [price for price in unit_price_mins if price is not None]
                    if prices and min(prices) > max_price:
                        continue
            else:
                d["decision_insight"] = _decision_insight_for_location(
                    loc, commute_anchor=commute_anchor, nearby_locs=nearby_locs
                )
            result.append(d)

    return JSONResponse(result)




@app.post("/api/locations")
async def api_add_location(payload: LocationCreateRequest) -> JSONResponse:
    import asyncio as _asyncio
    from commands.add import _build_location

    try:
        loc = _build_location(
            name=payload.name.strip(),
            address=payload.address.strip(),
            lat=payload.lat or 0.0,
            lon=payload.lon or 0.0,
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
            weight=payload.weight,
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

    if isinstance(stored, Apartment):
        if payload.manual_units:
            from models import Unit as _Unit
            manual = [
                _Unit(
                    floor_plan_name=u.floor_plan_name,
                    bed=u.bed,
                    bath=u.bath,
                    sqft_min=u.sqft_min,
                    sqft_max=u.sqft_max,
                    price_min=u.price_min,
                    price_max=u.price_max,
                )
                for u in payload.manual_units
            ]
            async with get_db() as db:
                await save_manual_units(db, stored.id, manual)
        else:
            _asyncio.create_task(_scrape_one_apartment(stored))

    return JSONResponse(_loc_to_dict(stored), status_code=201)


@app.post("/api/locations/enrich-from-url")
async def api_enrich_from_url(payload: UrlEnrichmentRequest) -> JSONResponse:
    try:
        enriched = await _enrich_from_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(UrlEnrichmentResponse(**enriched).model_dump())

@app.get("/api/locations/{loc_id}")
async def api_location_detail(loc_id: int) -> JSONResponse:
    async with get_db() as db:
        loc = await get_by_id(db, loc_id)
        if not loc:
            raise HTTPException(404, "Location not found")
        commute_anchor = await get_setting(db, "commute_anchor")
        all_locations = await list_all(db, None)
        nearby_locs = [
            l for l in all_locations
            if isinstance(l, Gym)
            or (isinstance(l, PointOfInterest) and (
                l.category == "grocery" or (l.weight or 0.0) >= 0.5
            ))
        ]
        d = _loc_to_dict(loc)
        if isinstance(loc, Apartment):
            units = await get_latest_units(db, loc.id)
            d["units"] = [_unit_to_dict(u) for u in units]
            d["available_count"] = sum(1 for u in units if u.available)
            d["decision_insight"] = _decision_insight_for_location(
                loc, units, commute_anchor, nearby_locs
            )
        else:
            d["decision_insight"] = _decision_insight_for_location(
                loc, commute_anchor=commute_anchor, nearby_locs=nearby_locs
            )
    return JSONResponse(d)


@app.delete("/api/locations/{loc_id}")
async def api_delete_location(loc_id: int) -> JSONResponse:
    async with get_db() as db:
        deleted = await delete_by_id(db, loc_id)
    if not deleted:
        raise HTTPException(404, "Location not found")
    return JSONResponse({"status": "deleted", "id": loc_id})


# ── Commute anchor settings ──────────────────────────────────────────────────

@app.get("/api/settings/commute-anchor")
async def api_get_commute_anchor() -> JSONResponse:
    async with get_db() as db:
        anchor = await get_setting(db, "commute_anchor")
    return JSONResponse(anchor or {})


@app.post("/api/settings/commute-anchor")
async def api_set_commute_anchor(payload: CommuteAnchorRequest) -> JSONResponse:
    async with get_db() as db:
        await set_setting(db, "commute_anchor", payload.model_dump())
    return JSONResponse({"status": "ok"})


@app.delete("/api/settings/commute-anchor")
async def api_delete_commute_anchor() -> JSONResponse:
    async with get_db() as db:
        await delete_setting(db, "commute_anchor")
    return JSONResponse({"status": "ok"})


@app.post("/api/locations/{loc_id}/units/manual")
async def api_save_manual_units(loc_id: int, payload: ManualUnitsRequest) -> JSONResponse:
    """Replace the manual floor plans for an apartment location."""
    from models import Unit as _Unit
    async with get_db() as db:
        loc = await get_by_id(db, loc_id)
        if not loc:
            raise HTTPException(404, "Location not found")
        if not isinstance(loc, Apartment):
            raise HTTPException(400, "Manual units are only supported for apartment locations")
        manual = [
            _Unit(
                floor_plan_name=u.floor_plan_name,
                bed=u.bed,
                bath=u.bath,
                sqft_min=u.sqft_min,
                sqft_max=u.sqft_max,
                price_min=u.price_min,
                price_max=u.price_max,
            )
            for u in payload.units
        ]
        await save_manual_units(db, loc_id, manual)
        units = await get_latest_units(db, loc_id)
    return JSONResponse([_unit_to_dict(u) for u in units])


@app.delete("/api/locations/{loc_id}/units/manual")
async def api_delete_manual_units(loc_id: int) -> JSONResponse:
    """Delete all manual floor plans for a location, re-enabling auto-scraping."""
    async with get_db() as db:
        loc = await get_by_id(db, loc_id)
        if not loc:
            raise HTTPException(404, "Location not found")
        await delete_manual_units(db, loc_id)
    return JSONResponse({"status": "manual units cleared", "id": loc_id})


async def _patch_extra_json(db: Any, loc_id: int, updates: dict[str, Any]) -> bool:
    """Merge updates into a location's extra_json field."""
    cur = await db.execute("SELECT extra_json FROM locations WHERE id=?", (loc_id,))
    row = await cur.fetchone()
    if not row:
        return False
    extra = json.loads(row["extra_json"] or "{}")
    extra.update(updates)
    await db.execute(
        "UPDATE locations SET extra_json=? WHERE id=?",
        (json.dumps(extra), loc_id),
    )
    await db.commit()
    return True


@app.patch("/api/locations/{loc_id}/amenities")
async def api_update_amenities(loc_id: int, payload: AmenitiesRequest) -> JSONResponse:
    """Set the amenities list for a location."""
    async with get_db() as db:
        ok = await _patch_extra_json(db, loc_id, {"amenities": payload.amenities})
    if not ok:
        raise HTTPException(404, "Location not found")
    return JSONResponse({"amenities": payload.amenities})


@app.patch("/api/locations/{loc_id}/pros-cons")
async def api_update_pros_cons(loc_id: int, payload: ProsConsRequest) -> JSONResponse:
    """Set the pros and cons lists for a location."""
    async with get_db() as db:
        ok = await _patch_extra_json(
            db, loc_id, {"pros": payload.pros, "cons": payload.cons}
        )
    if not ok:
        raise HTTPException(404, "Location not found")
    return JSONResponse({"pros": payload.pros, "cons": payload.cons})


@app.patch("/api/locations/{loc_id}/subtype")
async def api_update_subtype(loc_id: int, payload: SubtypeRequest) -> JSONResponse:
    """Set the housing subtype for an apartment location."""
    valid = {"apartment", "townhome", "condo", "studio"}
    if payload.subtype not in valid:
        raise HTTPException(400, f"subtype must be one of: {', '.join(sorted(valid))}")
    async with get_db() as db:
        ok = await _patch_extra_json(db, loc_id, {"subtype": payload.subtype})
    if not ok:
        raise HTTPException(404, "Location not found")
    return JSONResponse({"subtype": payload.subtype})


@app.patch("/api/locations/{loc_id}/verdict")
async def api_update_verdict(loc_id: int, payload: VerdictRequest) -> JSONResponse:
    """Set a thumbs-up / thumbs-down / neutral verdict on a location."""
    if payload.verdict not in ("up", "down", "neutral"):
        raise HTTPException(400, "verdict must be 'up', 'down', or 'neutral'")
    async with get_db() as db:
        ok = await _patch_extra_json(db, loc_id, {"verdict": payload.verdict})
    if not ok:
        raise HTTPException(404, "Location not found")
    return JSONResponse({"verdict": payload.verdict})


@app.patch("/api/locations/{loc_id}/top-pick")
async def patch_top_pick(loc_id: int, body: dict = Body(...)) -> JSONResponse:
    async with get_db() as db:
        cursor = await db.execute(
            "UPDATE locations SET is_top_pick=? WHERE id=?",
            [1 if body.get("is_top_pick") else 0, loc_id],
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Location not found")
    return JSONResponse({"ok": True})


@app.patch("/api/locations/{loc_id}/poi-category")
async def patch_poi_category(loc_id: int, body: dict = Body(...)) -> JSONResponse:
    async with get_db() as db:
        ok = await _patch_extra_json(db, loc_id, {"category": body.get("category") or None})
    if not ok:
        raise HTTPException(404, "Location not found")
    return JSONResponse({"ok": True})


@app.get("/api/geocode")
async def api_geocode(q: str = Query(...)) -> JSONResponse:
    """Geocode an address or place name. Returns {lat, lon, address}."""
    lat, lon, address = await _geocode_query(q)
    if lat is None:
        raise HTTPException(404, "Could not geocode that query")
    return JSONResponse({"lat": lat, "lon": lon, "address": address})


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
    type: str = Query("apartment"),
    area: str | None = Query(None),
    max_price: int = Query(3500),
    min_beds: int = Query(2),
    query: str | None = Query(None),
    min_rating: float = Query(4.0),
) -> JSONResponse:
    """Live search for apartments (Google Places) or gyms (Google Places). Results not stored."""
    if type == "gym":
        return await _api_search_gyms(query, min_rating)
    return await _api_search_apartments(area, max_price, min_beds, query)


async def _api_search_apartments(area: str | None, max_price: int, min_beds: int, query: str | None) -> JSONResponse:
    from scrapers.google_places import GooglePlacesScraper
    from config import SEARCH_BBOX, settings

    if not settings.google_places_api_key:
        raise HTTPException(503, "GOOGLE_PLACES_API_KEY not configured — apartment search requires Google Places API")

    scraper = GooglePlacesScraper()
    query_text = query or "apartment complex"
    try:
        results = await scraper.search_apartments(query_text, max_results=20)
    except Exception as e:
        raise HTTPException(500, f"Apartment search failed: {e}")

    # Filter by bounding box
    results = [
        r for r in results
        if SEARCH_BBOX["south"] <= r.lat <= SEARCH_BBOX["north"] and
           SEARCH_BBOX["west"] <= r.lon <= SEARCH_BBOX["east"]
    ]

    # Filter by area if specified
    if area:
        import math
        from config import AREA_CENTERS
        center = AREA_CENTERS.get(area.lower())
        if center:
            clat, clon = center
            R = 6371.0

            def nearby(apt, radius_km=8.0):
                dlat = math.radians(apt.lat - clat)
                dlon = math.radians(apt.lon - clon)
                a = math.sin(dlat/2)**2 + math.cos(math.radians(clat)) * math.cos(math.radians(apt.lat)) * math.sin(dlon/2)**2
                return R * 2 * math.asin(math.sqrt(a)) <= radius_km

            results = [r for r in results if nearby(r)]

    # Mark which are already tracked
    async with get_db() as db:
        existing = await list_all(db, location_type=LocationType.APARTMENT)
    tracked_names = {loc.name.lower() for loc in existing}

    out = []
    for r in results:
        d = _loc_to_dict(r)
        d["already_tracked"] = r.name.lower() in tracked_names
        out.append(d)

    return JSONResponse(out)


async def _api_search_gyms(query: str | None, min_rating: float) -> JSONResponse:
    from scrapers.google_places import GooglePlacesScraper
    from config import SEARCH_BBOX, settings

    if not settings.google_places_api_key:
        raise HTTPException(503, "GOOGLE_PLACES_API_KEY not configured")

    queries = [query] if query else _GYM_DISCOVERY_QUERIES
    scraper = GooglePlacesScraper()
    seen_ids: set[str] = set()
    gyms = []

    for q in queries:
        try:
            found = await scraper.search_gyms(q, max_results=10)
        except Exception as e:
            raise HTTPException(500, f"Gym search failed: {e}")
        for gym in found:
            place_id = gym.extra.get("google_place_id", "")
            if place_id and place_id in seen_ids:
                continue
            if place_id:
                seen_ids.add(place_id)
            if not (SEARCH_BBOX["south"] <= gym.lat <= SEARCH_BBOX["north"] and
                    SEARCH_BBOX["west"] <= gym.lon <= SEARCH_BBOX["east"]):
                continue
            if gym.rating is not None and gym.rating < min_rating:
                continue
            name_lower = gym.name.lower()
            if any(chain in name_lower for chain in _CHAIN_GYM_NAMES):
                continue
            gyms.append(gym)

    async with get_db() as db:
        existing = await list_all(db, location_type=LocationType.GYM)
    tracked_place_ids = {
        loc.google_place_id for loc in existing
        if hasattr(loc, "google_place_id") and loc.google_place_id
    }
    tracked_names = {loc.name.lower() for loc in existing}

    out = []
    for gym in gyms:
        d = _loc_to_dict(gym)
        place_id = gym.extra.get("google_place_id", "")
        d["already_tracked"] = (
            gym.name.lower() in tracked_names or
            (place_id and place_id in tracked_place_ids)
        )
        out.append(d)

    return JSONResponse(out)


@app.get("/api/inbox")
async def api_inbox() -> JSONResponse:
    """Return pending discovered locations (not yet approved or rejected)."""
    async with get_db() as db:
        pending = await disc_db.list_pending(db)
    return JSONResponse(pending)


@app.get("/api/inbox/count")
async def api_inbox_count() -> JSONResponse:
    """Return count of pending discoveries (for badge display)."""
    async with get_db() as db:
        count = await disc_db.pending_count(db)
    return JSONResponse({"count": count})


@app.post("/api/inbox/{discovery_id}/approve")
async def api_inbox_approve(discovery_id: int) -> JSONResponse:
    """Approve a discovery — adds it to tracked locations."""
    from commands.add import _build_location

    async with get_db() as db:
        item = await disc_db.get_by_id(db, discovery_id)
        if not item:
            raise HTTPException(404, "Discovery not found")

        data = item.get("data", {})
        try:
            loc = _build_location(
                name=item["name"],
                address=item["address"] or "",
                lat=item["lat"] or 0.0,
                lon=item["lon"] or 0.0,
                location_type_str=item["location_type"],
                website_url=data.get("website_url"),
                is_top_pick=False,
                notes=None,
                apartments_com_slug=data.get("apartments_com_slug"),
                google_place_id=data.get("google_place_id"),
                hours=data.get("hours"),
                equipment_highlights=[],
                health_system=None,
                category=None,
            )
            loc.rating = data.get("rating")
            loc.review_count = data.get("review_count")
            loc.phone = data.get("phone")
        except ValueError as e:
            raise HTTPException(400, str(e))

        await insert(db, loc)
        await disc_db.set_status(db, discovery_id, "approved")
        stored = await get_by_name(db, loc.name)

    if isinstance(loc, Apartment) and stored:
        import asyncio as _asyncio
        _asyncio.create_task(_scrape_one_apartment(stored))

    return JSONResponse(_loc_to_dict(stored) if stored else {"status": "added"}, status_code=201)


@app.post("/api/inbox/{discovery_id}/reject")
async def api_inbox_reject(discovery_id: int) -> JSONResponse:
    """Reject a discovery — removes it from the inbox."""
    async with get_db() as db:
        ok = await disc_db.set_status(db, discovery_id, "rejected")
    if not ok:
        raise HTTPException(404, "Discovery not found")
    return JSONResponse({"status": "rejected"})


@app.post("/api/discover")
async def api_trigger_discovery(type: str = Query("gym")) -> JSONResponse:
    """Manually trigger a background discovery run."""
    import asyncio as _asyncio

    if type == "gym":
        _asyncio.create_task(_run_gym_discovery())
    elif type == "apartment":
        _asyncio.create_task(_run_apartment_discovery())
    else:
        raise HTTPException(400, f"Unknown type '{type}'")

    return JSONResponse({"status": f"{type} discovery started"})


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
        data["weight"] = loc.weight
    # User-defined extra fields persisted in extra_json
    data["amenities"] = loc.extra.get("amenities", [])
    data["pros"] = loc.extra.get("pros", [])
    data["cons"] = loc.extra.get("cons", [])
    data["verdict"] = loc.extra.get("verdict", "neutral")
    data["subtype"] = loc.extra.get("subtype", "apartment")
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
        "is_manual": u.is_manual,
        "price_display": u.price_display,
        "size_display": u.size_display,
    }


async def _scrape_one_apartment(loc: Apartment) -> None:
    """Background task: scrape floor plans for a single apartment."""
    try:
        from scrapers.runner import ScrapeRunner
        runner = ScrapeRunner()
        await runner._scrape_apartment(loc)
    except Exception as e:
        logger.warning(f"Background scrape for '{loc.name}' failed: {e}")


def _slug_to_readable(slug: str) -> str:
    """Convert an apartments.com slug to a readable search query.
    e.g. 'waterford-bay-boca-raton-fl' -> 'Waterford Bay Boca Raton FL'
    """
    return " ".join(w.upper() if len(w) == 2 else w.capitalize() for w in slug.split("-"))


async def _geocode_query(query: str) -> tuple[float | None, float | None, str | None]:
    """Return (lat, lon, formatted_address) by geocoding a query string.
    Tries Google Places API first (if configured), then falls back to Nominatim (OSM).
    """
    import httpx as _httpx
    from config import settings

    # 1. Google Places Text Search
    if settings.google_places_api_key:
        try:
            headers = {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": settings.google_places_api_key,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
            }
            body = {
                "textQuery": query,
                "pageSize": 1,
                "locationBias": {
                    "circle": {
                        "center": {"latitude": 26.52, "longitude": -80.07},
                        "radius": 50000.0,
                    }
                },
            }
            async with _httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://places.googleapis.com/v1/places:searchText",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                places = resp.json().get("places", [])
                if places:
                    place = places[0]
                    loc_data = place.get("location", {})
                    lat = loc_data.get("latitude")
                    lon = loc_data.get("longitude")
                    address = place.get("formattedAddress")
                    if lat and lon:
                        return float(lat), float(lon), address
        except Exception as e:
            logger.debug(f"Google Places geocoding failed for '{query}': {e}")

    # 2. Nominatim fallback (free, no key needed)
    try:
        import urllib.parse as _urllib_parse
        encoded = _urllib_parse.quote(query)
        url = f"https://nominatim.openstreetmap.org/search?q={encoded}&format=json&limit=1&countrycodes=us"
        async with _httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "apartmenthunt/1.0"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            results = resp.json()
            if results:
                r = results[0]
                return float(r["lat"]), float(r["lon"]), r.get("display_name")
    except Exception as e:
        logger.debug(f"Nominatim geocoding failed for '{query}': {e}")

    return None, None, None


async def _enrich_from_url(raw_url: str) -> dict[str, Any]:
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
    suggested_address: str | None = None

    # If we don't have coordinates yet, try to geocode
    if lat is None and lon is None:
        geocode_query: str | None = None
        if apartments_slug:
            geocode_query = _slug_to_readable(apartments_slug)
        elif suggested_name:
            geocode_query = suggested_name

        if geocode_query:
            lat, lon, suggested_address = await _geocode_query(geocode_query)

        # Refine suggested_name from the geocoding query (cleaner than URL hints)
        if apartments_slug and not suggested_name:
            # Use only the property name portion of the slug (before city/state)
            parts = apartments_slug.split("-")
            # Heuristic: drop trailing 2-letter state code and city words
            # e.g. "waterford-bay-boca-raton-fl" -> "Waterford Bay"
            # Keep words until we hit something that looks like a city or state
            _FL_CITIES = {"boca", "raton", "boynton", "beach", "delray", "lake", "worth",
                          "palm", "west", "lantana", "greenacres", "wellington"}
            name_parts = []
            for p in parts:
                if len(p) == 2 or p.lower() in _FL_CITIES:
                    break
                name_parts.append(p.capitalize())
            if name_parts:
                suggested_name = " ".join(name_parts)

    return {
        "url": raw_url,
        "normalized_url": normalized,
        "inferred_type": inferred_type,
        "suggested_name": suggested_name,
        "suggested_address": suggested_address,
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
