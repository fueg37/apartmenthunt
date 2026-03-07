"""Realtor.com rental scraper via Playwright.

Realtor.com uses a path-based filter URL and embeds all listing data in a
<script id="__NEXT_DATA__"> JSON block.  We use Playwright so Cloudflare's
JS cookie challenge is handled automatically.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from models import Apartment, Unit
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.realtor.com"

# Cities in Palm Beach County — Realtor.com searches per-city, not county-wide
_PBC_CITIES = [
    "Boca-Raton_FL",
    "West-Palm-Beach_FL",
    "Delray-Beach_FL",
    "Boynton-Beach_FL",
    "Lake-Worth_FL",
    "Palm-Beach-Gardens_FL",
]


class ZillowScraper(BaseScraper):
    """Scrapes Realtor.com rentals via Playwright (drop-in replacement for Zillow)."""

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
        seen: set[str] = set()

        for city_slug in _PBC_CITIES:
            # Realtor.com path filter format: /apartments/City_ST/price-MIN-MAX/beds-N/
            url = f"{_BASE}/apartments/{city_slug}/price-{min_price}-{max_price}/beds-{min_beds}/"
            logger.info(f"Realtor.com: {url}")

            page = await self._new_page()
            try:
                ok = await self._safe_goto(page, url, wait_until="domcontentloaded")
                if not ok:
                    logger.warning(f"Could not load Realtor.com for {city_slug}")
                    continue

                title = await page.title()
                logger.debug(f"Realtor.com {city_slug} title: {title!r}")

                # Wait for listings to hydrate
                try:
                    await page.wait_for_selector(
                        "script#__NEXT_DATA__, [data-testid='card-content'], .property-list",
                        timeout=10_000,
                    )
                except Exception:
                    pass

                html = await page.content()
                city_results = _parse_page(html)
                logger.info(f"Realtor.com {city_slug}: {len(city_results)} listings")

                for apt in city_results:
                    key = apt.name.lower()
                    if key not in seen:
                        seen.add(key)
                        results.append(apt)

            except Exception as e:
                logger.warning(f"Realtor.com {city_slug} failed: {e}")
            finally:
                await page.close()

        logger.info(f"Realtor.com search complete: {len(results)} unique listings")
        return results


def _parse_page(html: str) -> list[Apartment]:
    from bs4 import BeautifulSoup
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

    # Fallback: look for window.__data__ or similar embedded JSON arrays
    for script in soup.find_all("script"):
        text = script.string or ""
        if '"listing_id"' in text or '"property_id"' in text:
            m = re.search(r'(\[{.*?"listing_id".*?}\])', text, re.DOTALL)
            if m:
                try:
                    items = json.loads(m.group(1))
                    return [a for a in (_apt_from_ld(i) for i in items) if a]
                except json.JSONDecodeError:
                    pass

    logger.debug("Realtor.com: no listing data found in page")
    return []


def _extract_from_next_data(data: dict) -> list[Apartment]:
    candidates: list[Any] = []

    # Try known paths in Realtor.com's Next.js state
    try:
        candidates = (
            data["props"]["pageProps"]["searchResults"]
            ["data"]["home_search"]["results"]
        )
    except (KeyError, TypeError):
        pass

    if not candidates:
        try:
            candidates = data["props"]["pageProps"]["properties"]
        except (KeyError, TypeError):
            pass

    if not candidates:
        candidates = _find_listings_recursive(data)

    return [a for a in (_apt_from_realtor(item) for item in candidates) if a]


def _find_listings_recursive(obj: Any, depth: int = 0) -> list[dict]:
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
    try:
        location = item.get("location", {}) or {}
        address_obj = location.get("address", {}) or {}

        street = address_obj.get("line") or ""
        city = address_obj.get("city") or ""
        state = address_obj.get("state_code") or ""
        postal = address_obj.get("postal_code") or ""

        name = (item.get("community") or {}).get("name") or street or item.get("permalink") or "Unknown"
        address = ", ".join(p for p in [street, city, state, postal] if p) or name

        coordinate = location.get("coordinate", {}) or {}
        lat = float(coordinate.get("lat") or 0)
        lon = float(coordinate.get("lon") or 0)

        detail_url = item.get("href") or ""
        if detail_url and not detail_url.startswith("http"):
            detail_url = _BASE + detail_url

        price_min: int | None = None
        for price_field in ("list_price", "price"):
            raw = item.get(price_field)
            if raw:
                try:
                    price_min = int(float(raw))
                    break
                except (ValueError, TypeError):
                    pass
        if price_min is None:
            try:
                price_min = int((item.get("community") or {}).get("price_min") or 0) or None
            except (ValueError, TypeError):
                pass

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
    try:
        name = item.get("community_name") or (item.get("address") or {}).get("line") or "Unknown"
        addr = item.get("address", {}) or {}
        address = ", ".join(p for p in [addr.get("line", ""), addr.get("city", ""), addr.get("state_code", "")] if p) or name
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
