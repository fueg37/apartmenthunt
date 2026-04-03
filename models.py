from __future__ import annotations

import math
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LocationType(str, Enum):
    APARTMENT = "apartment"
    GYM = "gym"
    HOSPITAL = "hospital"
    POI = "poi"


class Unit(BaseModel):
    floor_plan_name: str
    bed: int
    bath: float
    sqft_min: int | None = None
    sqft_max: int | None = None
    price_min: int | None = None   # None means "Contact for pricing"
    price_max: int | None = None
    available: bool = True
    move_in_date: date | None = None
    unit_number: str | None = None
    scraped_at: datetime | None = None

    @property
    def price_display(self) -> str:
        if self.price_min is None:
            return "Contact for pricing"
        if self.price_max and self.price_max != self.price_min:
            return f"${self.price_min:,} – ${self.price_max:,}/mo"
        return f"${self.price_min:,}/mo"

    @property
    def size_display(self) -> str:
        parts = [f"{self.bed}BD/{self.bath:.0f}BA"]
        if self.sqft_min:
            if self.sqft_max and self.sqft_max != self.sqft_min:
                parts.append(f"{self.sqft_min:,}–{self.sqft_max:,} sqft")
            else:
                parts.append(f"{self.sqft_min:,} sqft")
        return " · ".join(parts)


class Location(BaseModel):
    id: int | None = None
    name: str
    address: str
    lat: float
    lon: float
    location_type: LocationType
    rating: float | None = None
    review_count: int | None = None
    website_url: str | None = None
    phone: str | None = None
    is_top_pick: bool = False
    notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    last_scraped: datetime | None = None
    scraped_ok: bool | None = None

    def distance_km(self, other: Location) -> float:
        """Haversine distance in kilometres."""
        R = 6371.0
        lat1, lon1 = math.radians(self.lat), math.radians(self.lon)
        lat2, lon2 = math.radians(other.lat), math.radians(other.lon)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.asin(math.sqrt(a))

    def drive_minutes(self, other: Location, mph: float = 24.0) -> int:
        """Estimated drive time in minutes at average urban speed."""
        km = self.distance_km(other)
        miles = km * 0.621371
        return round(miles / mph * 60)

    @property
    def rating_display(self) -> str:
        if self.rating is None:
            return "–"
        stars = f"⭐ {self.rating:.1f}"
        if self.review_count:
            stars += f" ({self.review_count:,})"
        return stars


class Apartment(Location):
    location_type: LocationType = LocationType.APARTMENT
    apartments_com_slug: str | None = None
    units: list[Unit] = Field(default_factory=list)

    @property
    def available_units(self) -> list[Unit]:
        return [u for u in self.units if u.available]

    @property
    def price_range_display(self) -> str:
        prices = [u.price_min for u in self.units if u.price_min is not None]
        if not prices:
            return "Contact for pricing"
        lo, hi = min(prices), max(prices)
        if lo == hi:
            return f"${lo:,}/mo"
        return f"${lo:,} – ${hi:,}/mo"


class Gym(Location):
    location_type: LocationType = LocationType.GYM
    google_place_id: str | None = None
    hours: str | None = None
    equipment_highlights: list[str] = Field(default_factory=list)


class Hospital(Location):
    location_type: LocationType = LocationType.HOSPITAL
    google_place_id: str | None = None
    hours: str | None = None
    health_system: str | None = None


class PointOfInterest(Location):
    location_type: LocationType = LocationType.POI
    category: str | None = None
    weight: float | None = None  # 0.0–1.0 priority; 1.0 = highest (e.g. Whole Foods)


# Map location_type → model class for deserialization
LOCATION_MODELS: dict[LocationType, type[Location]] = {
    LocationType.APARTMENT: Apartment,
    LocationType.GYM: Gym,
    LocationType.HOSPITAL: Hospital,
    LocationType.POI: PointOfInterest,
}


def location_from_row(row: dict) -> Location:
    """Hydrate the correct Location subclass from a DB row dict."""
    import json
    lt = LocationType(row["location_type"])
    cls = LOCATION_MODELS[lt]
    extra = json.loads(row.get("extra_json") or "{}")
    last_scraped = None
    if row.get("last_scraped"):
        last_scraped = datetime.fromisoformat(row["last_scraped"])
    return cls(
        id=row["id"],
        name=row["name"],
        address=row["address"] or "",
        lat=row["lat"] or 0.0,
        lon=row["lon"] or 0.0,
        location_type=lt,
        rating=row.get("rating"),
        review_count=row.get("review_count"),
        website_url=row.get("website_url"),
        phone=row.get("phone"),
        is_top_pick=bool(row.get("is_top_pick", 0)),
        notes=row.get("notes"),
        extra=extra,
        last_scraped=last_scraped,
        scraped_ok=bool(row["scraped_ok"]) if row.get("scraped_ok") is not None else None,
        **_type_extras(lt, extra),
    )


def _type_extras(lt: LocationType, extra: dict) -> dict:
    if lt == LocationType.APARTMENT:
        return {"apartments_com_slug": extra.get("apartments_com_slug")}
    if lt == LocationType.GYM:
        return {
            "google_place_id": extra.get("google_place_id"),
            "hours": extra.get("hours"),
            "equipment_highlights": extra.get("equipment_highlights", []),
        }
    if lt == LocationType.HOSPITAL:
        return {
            "google_place_id": extra.get("google_place_id"),
            "hours": extra.get("hours"),
            "health_system": extra.get("health_system"),
        }
    if lt == LocationType.POI:
        return {"category": extra.get("category"), "weight": extra.get("weight")}
    return {}
