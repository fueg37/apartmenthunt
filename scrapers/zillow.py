"""Realtor.com rental scraper (replaces Zillow — same data, no bot detection).

Realtor.com embeds all search results in a <script id="__NEXT_DATA__"> JSON
block.  A plain httpx request with browser-like headers is sufficient; no
Playwright / JS evaluation required.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from models import Apartment, Unit

logger = logging.getLogger(__name__)

_BASE = "https://www.realtor.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class ZillowScraper:
    """Scrapes Realtor.com rentals (presents as ZillowScraper for drop-in compatibility)."""

    async def close(self) -> None:  # nothing to clean up
        pass

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
        results: list[Apartment] = []

        async with httpx.AsyncClient(
            headers=_HEADERS, follow_redirects=True, timeout=30
        ) as client:
            for page_num in range(1, 4):
                page_results = await _fetch_page(
                    client, page_num, min_price, max_price, min_beds
                )
                if not page_results:
                    break
                results.extend(page_results)
                logger.info(f"Realtor.com page {page_num}: {len(results)} total")
                if len(page_results) < 20:
                    break  # last page

        # Deduplicate by name
        seen: set[str] = set()
        deduped: list[Apartment] = []
        for apt in results:
            key = apt.name.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(apt)

        logger.info(f"Realtor.com search complete: {len(deduped)} unique listings")
        return deduped


async def _fetch_page(
    client: httpx.AsyncClient,
    page: int,
    min_price: int,
    max_price: int,
    min_beds: int,
) -> list[Apartment]:
    # Realtor.com URL pattern for Palm Beach County rentals
    # e.g. /apartments/Palm-Beach-County_FL/pg-2
    base_path = "/apartments/Palm-Beach-County_FL"
    path = base_path if page == 1 else f"{base_path}/pg-{page}"

    # Price + beds filter via query params
    params = {
        "price_min": min_price,
        "price_max": max_price,
        f"beds_min": min_beds,
    }
    url = f"{_BASE}{path}?{urlencode(params)}"
    logger.info(f"Realtor.com: {url}")

    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Realtor.com returned HTTP {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise RuntimeError(f"Realtor.com request error: {e}") from e

    return _parse_page(resp.text)


def _parse_page(html: str) -> list[Apartment]:
    soup = BeautifulSoup(html, "lxml")

    # Primary: __NEXT_DATA__ JSON blob
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            data = json.loads(tag.string)
            results = _extract_from_next_data(data)
            if results:
                return results
        except (json.JSONDecodeError, KeyError):
            pass

    # Fallback: look for JSON-LD or embedded data arrays
    for script in soup.find_all("script"):
        text = script.string or ""
        if '"listing_id"' in text or '"property_id"' in text:
            m = re.search(r'\[(\{.*?"listing_id".*?\})\]', text, re.DOTALL)
            if m:
                try:
                    items = json.loads("[" + m.group(1) + "]")
                    return [a for a in (_apt_from_ld(i) for i in items) if a]
                except json.JSONDecodeError:
                    pass

    logger.warning("Realtor.com: could not find listing data in page")
    return []


def _extract_from_next_data(data: dict) -> list[Apartment]:
    """Walk Realtor.com's __NEXT_DATA__ tree to find property listings."""
    # Path varies by page version; try several known locations
    candidates: list[Any] = []

    # Try pageProps.searchResults.data.home_search.results
    try:
        candidates = (
            data["props"]["pageProps"]["searchResults"]
            ["data"]["home_search"]["results"]
        )
    except (KeyError, TypeError):
        pass

    if not candidates:
        # Try pageProps.properties
        try:
            candidates = data["props"]["pageProps"]["properties"]
        except (KeyError, TypeError):
            pass

    if not candidates:
        # Try a recursive search for a list containing items with "listing_id"
        candidates = _find_listings_recursive(data)

    return [a for a in (_apt_from_realtor(item) for item in candidates) if a]


def _find_listings_recursive(obj: Any, depth: int = 0) -> list[dict]:
    """Recursively find a list of dicts that look like property listings."""
    if depth > 8:
        return []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        if any(k in obj[0] for k in ("listing_id", "property_id", "permalink", "list_price")):
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            result = _find_listings_recursive(v, depth + 1)
            if result:
                return result
    return []


def _apt_from_realtor(item: dict) -> Apartment | None:
    """Convert a Realtor.com listing dict to an Apartment."""
    try:
        location = item.get("location", {}) or {}
        address_obj = location.get("address", {}) or {}

        street = address_obj.get("line") or ""
        city = address_obj.get("city") or ""
        state = address_obj.get("state_code") or ""
        postal = address_obj.get("postal_code") or ""

        name = item.get("community", {}).get("name") if item.get("community") else None
        if not name:
            name = street or item.get("permalink") or "Unknown"

        address = ", ".join(p for p in [street, city, state, postal] if p) or name

        coordinate = location.get("coordinate", {}) or {}
        lat = float(coordinate.get("lat") or 0)
        lon = float(coordinate.get("lon") or 0)

        detail_url = item.get("href") or ""
        if detail_url and not detail_url.startswith("http"):
            detail_url = _BASE + detail_url

        # Price
        price_min: int | None = None
        list_price = item.get("list_price") or item.get("price")
        if list_price:
            try:
                price_min = int(float(list_price))
            except (ValueError, TypeError):
                pass
        if price_min is None:
            try:
                price_min = int(item["community"]["price_min"])
            except (KeyError, TypeError, ValueError):
                pass

        # Beds / baths
        desc = item.get("description", {}) or {}
        try:
            beds = int(desc.get("beds") or desc.get("beds_min") or 2)
        except (ValueError, TypeError):
            beds = 2
        try:
            baths = float(desc.get("baths") or desc.get("baths_min") or 1)
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


def _apt_from_ld(item: dict) -> Apartment | None:
    """Fallback: parse a simpler listing dict shape."""
    try:
        name = item.get("community_name") or item.get("address", {}).get("line") or "Unknown"
        address_obj = item.get("address", {}) or {}
        address = ", ".join(
            p for p in [
                address_obj.get("line", ""),
                address_obj.get("city", ""),
                address_obj.get("state_code", ""),
            ] if p
        ) or name
        lat = float(item.get("lat") or item.get("latitude") or 0)
        lon = float(item.get("lon") or item.get("longitude") or 0)
        detail_url = item.get("href", "")
        price_min = None
        raw = item.get("price") or item.get("list_price")
        if raw:
            try:
                price_min = int(float(raw))
            except (ValueError, TypeError):
                pass
        apt = Apartment(name=name, address=address, lat=lat, lon=lon, website_url=detail_url)
        if price_min:
            apt.units = [Unit(floor_plan_name="Unit", bed=2, bath=1, price_min=price_min, price_max=price_min)]
        return apt
    except Exception:
        return None
