"""RentCast API scraper for apartment listings.

Replaces the Playwright-based apartments.com and property-site scrapers with
calls to the RentCast REST API (api.rentcast.io). No browser automation needed.

Free tier: 50 API calls/month — plenty for a personal tool.
Set RENTCAST_API_KEY in .env to enable.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

import httpx

from config import settings
from models import Apartment, Unit

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.rentcast.io/v1"

# Zip codes covering Boca Raton → Lake Worth Beach search area
_PALM_BEACH_ZIPS = [
    "33431", "33432", "33433", "33434", "33486",  # Boca Raton
    "33435", "33436", "33437",                     # Boynton Beach
    "33444", "33445", "33446", "33483",            # Delray Beach
    "33460", "33461",                              # Lake Worth Beach
]


class RentCastScraper:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.rentcast_api_key
        if not self.api_key:
            raise ValueError("RENTCAST_API_KEY is not set in .env")

    async def scrape(self, apartment: Apartment) -> Apartment:
        """Fetch current unit listings for a tracked apartment complex.

        Searches within ~0.3 km of the apartment's coordinates. Falls back to
        city-level search if coordinates are missing.
        """
        listings: list[dict] = []

        if apartment.lat and apartment.lon and not (apartment.lat == 0.0 and apartment.lon == 0.0):
            listings = await self._fetch_by_location(apartment.lat, apartment.lon)

        if not listings:
            city = _city_from_address(apartment.address)
            if city:
                logger.debug(f"{apartment.name}: falling back to city search for '{city}'")
                all_city = await self._fetch_by_city(city)
                # Try to narrow down by proximity if we have coordinates
                if apartment.lat and apartment.lon:
                    listings = _filter_by_proximity(all_city, apartment.lat, apartment.lon, radius_km=0.5)
                else:
                    listings = all_city

        apartment.units = [_to_unit(l) for l in listings if l.get("price")]
        return apartment

    async def search(
        self,
        min_price: int = 1800,
        max_price: int = 3500,
        min_beds: int = 2,
    ) -> list[Apartment]:
        """Discover available rentals across the Palm Beach area.

        Queries each zip code in _PALM_BEACH_ZIPS and groups individual unit
        listings into Apartment objects by street address.
        """
        all_listings: list[dict] = []
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"X-Api-Key": self.api_key},
        ) as client:
            for zip_code in _PALM_BEACH_ZIPS:
                found = await _get_listings(
                    client,
                    zipCode=zip_code,
                    status="Active",
                    propertyType="Apartment",
                    limit=500,
                )
                all_listings.extend(found)

        filtered = [
            l for l in all_listings
            if (l.get("bedrooms") or 0) >= min_beds
            and l.get("price") is not None
            and min_price <= int(l["price"]) <= max_price
        ]

        return _group_to_apartments(filtered)

    async def _fetch_by_location(self, lat: float, lon: float, radius_km: float = 0.3) -> list[dict]:
        radius_miles = radius_km * 0.621371
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"X-Api-Key": self.api_key},
        ) as client:
            return await _get_listings(
                client,
                latitude=lat,
                longitude=lon,
                radius=radius_miles,
                status="Active",
                propertyType="Apartment",
                limit=100,
            )

    async def _fetch_by_city(self, city: str) -> list[dict]:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"X-Api-Key": self.api_key},
        ) as client:
            return await _get_listings(
                client,
                city=city,
                state="FL",
                status="Active",
                propertyType="Apartment",
                limit=200,
            )

    async def close(self) -> None:
        pass  # No persistent connection to close


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_listings(client: httpx.AsyncClient, **params) -> list[dict]:
    """Call the RentCast long-term rental listings endpoint."""
    try:
        resp = await client.get(
            f"{_BASE_URL}/listings/rental/long-term",
            params={k: v for k, v in params.items() if v is not None},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except httpx.HTTPStatusError as e:
        logger.warning(f"RentCast API error {e.response.status_code}: {e.response.text[:200]}")
        return []
    except Exception as e:
        logger.warning(f"RentCast request failed: {e}")
        return []


def _to_unit(listing: dict) -> Unit:
    """Convert a RentCast listing dict to a Unit model."""
    beds = int(listing.get("bedrooms") or 0)
    baths = float(listing.get("bathrooms") or 0)
    sqft = listing.get("squareFootage")
    unit_num = listing.get("addressLine2") or listing.get("unit")
    return Unit(
        floor_plan_name=f"{beds}BD/{baths:.0f}BA",
        bed=beds,
        bath=baths,
        sqft_min=int(sqft) if sqft else None,
        price_min=int(listing["price"]),
        available=True,
        unit_number=unit_num or None,
        scraped_at=datetime.now(),
    )


def _group_to_apartments(listings: list[dict]) -> list[Apartment]:
    """Group individual unit listings into Apartment objects by street address."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for listing in listings:
        key = listing.get("addressLine1") or listing.get("formattedAddress", "Unknown")
        groups[key].append(listing)

    apartments = []
    for _, group in groups.items():
        first = group[0]
        apt = Apartment(
            name=first.get("addressLine1", "Unknown"),
            address=first.get("formattedAddress", ""),
            lat=float(first.get("latitude") or 0.0),
            lon=float(first.get("longitude") or 0.0),
            units=[_to_unit(l) for l in group if l.get("price")],
        )
        apartments.append(apt)

    return apartments


def _city_from_address(address: str) -> str | None:
    """Extract city from a formatted address string.

    E.g. "123 Main St, Boynton Beach, FL 33435" → "Boynton Beach"
    """
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        return parts[-2].strip()
    if len(parts) == 2:
        # "City, FL 33435" — take first part
        return parts[0].strip()
    return None


def _filter_by_proximity(
    listings: list[dict],
    lat: float,
    lon: float,
    radius_km: float,
) -> list[dict]:
    """Filter listings to those within radius_km of the given coordinates."""
    import math

    result = []
    for l in listings:
        llat = l.get("latitude")
        llon = l.get("longitude")
        if llat is None or llon is None:
            continue
        R = 6371.0
        dlat = math.radians(float(llat) - lat)
        dlon = math.radians(float(llon) - lon)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat)) * math.cos(math.radians(float(llat)))
             * math.sin(dlon / 2) ** 2)
        dist_km = R * 2 * math.asin(math.sqrt(a))
        if dist_km <= radius_km:
            result.append(l)
    return result
