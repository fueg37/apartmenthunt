"""Google Places API scraper for gyms, hospitals, and POIs."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from config import settings
from models import Apartment, Gym, Hospital, Location, LocationType

logger = logging.getLogger(__name__)

_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACE_DETAIL_URL = "https://places.googleapis.com/v1/places/{place_id}"

_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.rating",
    "places.userRatingCount",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.regularOpeningHours",
])


class GooglePlacesScraper:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.google_places_api_key
        if not self.api_key:
            raise ValueError("GOOGLE_PLACES_API_KEY is not set in .env")

    async def search_and_update(self, loc: Location) -> Location:
        """Search for a location by name+address and update its metadata."""
        query = f"{loc.name} {loc.address}"
        results = await self._search_text(query)
        if not results:
            logger.warning(f"No Google Places result for '{loc.name}'")
            return loc

        place = results[0]
        self._apply_place_data(loc, place)
        return loc

    async def search_gyms(self, query: str, max_results: int = 10) -> list[Gym]:
        """Search for gyms by free-text query."""
        results = await self._search_text(query, max_results=max_results)
        gyms: list[Gym] = []
        for place in results:
            gym = Gym(
                name=place.get("displayName", {}).get("text", "Unknown"),
                address=place.get("formattedAddress", ""),
                lat=place.get("location", {}).get("latitude", 0.0),
                lon=place.get("location", {}).get("longitude", 0.0),
            )
            self._apply_place_data(gym, place)
            gyms.append(gym)
        return gyms

    async def search_apartments(self, query: str, max_results: int = 10) -> list[Apartment]:
        """Search for apartment complexes by free-text query."""
        results = await self._search_text(query, max_results=max_results)
        apartments: list[Apartment] = []
        for place in results:
            apt = Apartment(
                name=place.get("displayName", {}).get("text", "Unknown"),
                address=place.get("formattedAddress", ""),
                lat=place.get("location", {}).get("latitude", 0.0),
                lon=place.get("location", {}).get("longitude", 0.0),
            )
            self._apply_place_data(apt, place)
            apartments.append(apt)
        return apartments

    async def _search_text(self, query: str, max_results: int = 5) -> list[dict]:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        body = {
            "textQuery": query,
            "pageSize": max_results,
            "locationBias": {
                "circle": {
                    "center": {"latitude": 26.52, "longitude": -80.07},
                    "radius": 40000.0,  # 40km covers the Palm Beach area
                }
            },
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(_PLACES_URL, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json().get("places", [])

    def _apply_place_data(self, loc: Location, place: dict) -> None:
        loc.phone = place.get("nationalPhoneNumber")
        loc.website_url = loc.website_url or place.get("websiteUri")
        if place.get("rating"):
            loc.rating = float(place["rating"])
        if place.get("userRatingCount"):
            loc.review_count = int(place["userRatingCount"])

        place_id = place.get("id")
        if place_id:
            loc.extra["google_place_id"] = place_id
            if isinstance(loc, (Gym, Hospital)):
                loc.google_place_id = place_id

        hours_data = place.get("regularOpeningHours", {})
        weekday_text = hours_data.get("weekdayDescriptions", [])
        if weekday_text and isinstance(loc, (Gym, Hospital)):
            loc.hours = " | ".join(weekday_text[:3])  # First 3 days for brevity

        loc_data = place.get("location", {})
        if loc_data.get("latitude") and loc.lat == 0.0:
            loc.lat = float(loc_data["latitude"])
        if loc_data.get("longitude") and loc.lon == 0.0:
            loc.lon = float(loc_data["longitude"])
