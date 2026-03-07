"""Zillow rental scraper via pyzill.

pyzill uses curl_cffi to spoof TLS/JA3 fingerprints at the protocol level,
bypassing Zillow's PerimeterX detection without a headless browser.
Works from a residential IP without proxies for occasional searches.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from models import Apartment, Unit

logger = logging.getLogger(__name__)

_BASE = "https://www.zillow.com"


class ZillowScraper:
    """Thin async wrapper around pyzill.for_rent()."""

    async def close(self) -> None:
        pass  # no resources to clean up

    async def scrape(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def search(
        self,
        bbox: dict | None = None,
        min_price: int = 1800,
        max_price: int = 3500,
        min_beds: int = 2,
        **_: Any,
    ) -> list[Apartment]:
        from config import SEARCH_BBOX
        import pyzill

        bb = bbox or SEARCH_BBOX

        loop = asyncio.get_event_loop()
        raw: dict = await loop.run_in_executor(
            None,
            lambda: pyzill.for_rent(
                pagination=1,
                search_value="Palm Beach County, FL",
                is_entire_place=True,
                is_room=False,
                min_beds=min_beds,
                max_beds=None,
                min_bathrooms=None,
                max_bathrooms=None,
                min_price=min_price,
                max_price=max_price,
                ne_lat=bb["north"],
                ne_long=bb["east"],
                sw_lat=bb["south"],
                sw_long=bb["west"],
                zoom_value=11,
                proxy_url=None,
            ),
        )

        results = _parse_response(raw)
        logger.info(f"Zillow: {len(results)} listings")
        return results


def _parse_response(data: dict) -> list[Apartment]:
    """Parse pyzill response — use mapResults (all listings, up to 500)."""
    if not isinstance(data, dict):
        logger.warning(f"Zillow: unexpected response type {type(data)}")
        return []

    # pyzill doc: use mapResults (has all listings regardless of pagination)
    items = data.get("mapResults") or data.get("listResults") or []

    # Also check nested shapes pyzill sometimes returns
    if not items:
        for key in ("cat1", "cat2"):
            cat = data.get(key, {})
            if isinstance(cat, dict):
                sr = cat.get("searchResults", {})
                items = sr.get("mapResults") or sr.get("listResults") or []
                if items:
                    break

    results: list[Apartment] = []
    seen: set[str] = set()
    for item in items:
        apt = _apt_from_item(item)
        if apt:
            key = apt.name.lower()
            if key not in seen:
                seen.add(key)
                results.append(apt)

    return results


def _apt_from_item(item: dict) -> Apartment | None:
    try:
        name = (
            item.get("buildingName")
            or item.get("address")
            or item.get("addressStreet")
        )
        if not name:
            return None

        address_parts = [
            item.get("addressStreet", ""),
            item.get("addressCity", ""),
            item.get("addressState", ""),
            item.get("addressZipcode", ""),
        ]
        address = ", ".join(p for p in address_parts if p) or name

        ll = item.get("latLong") or {}
        lat = float(ll.get("latitude") or item.get("latitude") or 0)
        lon = float(ll.get("longitude") or item.get("longitude") or 0)

        detail_url = item.get("detailUrl", "")
        if detail_url and not detail_url.startswith("http"):
            detail_url = _BASE + detail_url

        price_min = _parse_price(
            str(item.get("price") or item.get("unformattedPrice") or "")
        )

        try:
            beds = int(item.get("beds") or item.get("bedrooms") or 2)
        except (ValueError, TypeError):
            beds = 2

        try:
            baths = float(item.get("baths") or item.get("bathrooms") or 1)
        except (ValueError, TypeError):
            baths = 1.0

        apt = Apartment(name=name, address=address, lat=lat, lon=lon, website_url=detail_url)
        if price_min:
            apt.units = [Unit(
                floor_plan_name=f"{beds}BR/{baths:.0f}BA",
                bed=beds,
                bath=baths,
                price_min=price_min,
                price_max=price_min,
            )]
        return apt
    except Exception:
        return None


def _parse_price(text: str) -> int | None:
    if not text:
        return None
    try:
        return int(float(text))
    except (ValueError, TypeError):
        pass
    m = re.search(r"\$?([\d,]+)", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return None
