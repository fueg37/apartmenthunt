"""Zillow rental scraper — intercepts internal GetSearchPageState API."""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

from models import Apartment, Unit
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

_BASE = "https://www.zillow.com"


class ZillowScraper(BaseScraper):

    async def scrape(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def search(
        self,
        bbox: dict,
        min_price: int = 1800,
        max_price: int = 3500,
        min_beds: int = 2,
    ) -> list[Apartment]:
        """Search Zillow rentals within a bounding box via API interception."""
        search_query = {
            "pagination": {"currentPage": 1},
            "isMapVisible": True,
            "mapBounds": {
                "west": bbox["west"],
                "east": bbox["east"],
                "south": bbox["south"],
                "north": bbox["north"],
            },
            "filterState": {
                "isForRent": {"value": True},
                "isForSaleByAgent": {"value": False},
                "isForSaleByOwner": {"value": False},
                "isNewConstruction": {"value": False},
                "isAuction": {"value": False},
                "isComingSoon": {"value": False},
                "price": {"min": min_price, "max": max_price},
                "beds": {"min": min_beds},
            },
            "isListVisible": True,
        }
        qs = urlencode({"searchQueryState": json.dumps(search_query, separators=(",", ":"))})
        url = f"{_BASE}/palm-beach-county-fl/rentals/?{qs}"
        logger.info(f"Zillow search URL: {url}")

        # Capture Zillow's internal search API responses
        api_payloads: list[dict] = []

        async def _on_response(response):
            try:
                if "GetSearchPageState" in response.url and response.status == 200:
                    data = await response.json()
                    api_payloads.append(data)
                    logger.debug(f"Captured Zillow API response: {response.url[:80]}")
            except Exception:
                pass

        page = await self._new_page()
        page.on("response", _on_response)

        results: list[Apartment] = []
        try:
            ok = await self._safe_goto(page, url, wait_until="networkidle")
            if not ok:
                raise RuntimeError("Could not load Zillow — possible bot block")

            title = await page.title()
            logger.info(f"Zillow page title: {title!r}")
            title_lower = title.lower()
            if any(kw in title_lower for kw in ("access denied", "captcha", "blocked", "robot", "just a moment", "403")):
                raise RuntimeError(f"Bot detection triggered — page title: {title!r}")

            # Scroll to trigger lazy loading and additional API calls
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await self._human_delay()
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self._human_delay()

            if api_payloads:
                logger.info(f"Got {len(api_payloads)} Zillow API response(s)")
                for payload in api_payloads:
                    results.extend(_parse_zillow_payload(payload))
            else:
                # Fallback: parse HTML listing cards
                logger.info("No Zillow API responses intercepted, falling back to HTML")
                html = await page.content()
                results = _parse_zillow_html(html)

            logger.info(f"Zillow page 1: {len(results)} listings")

            # Pagination — up to 2 more pages
            for page_num in range(2, 4):
                next_btn = await page.query_selector(
                    "a[title='Next page'], button[aria-label='Next page'], a[rel='next']"
                )
                if not next_btn:
                    break
                api_payloads.clear()
                await next_btn.click()
                await self._human_delay()
                if api_payloads:
                    for payload in api_payloads:
                        results.extend(_parse_zillow_payload(payload))
                else:
                    html = await page.content()
                    results.extend(_parse_zillow_html(html))
                logger.info(f"Zillow page {page_num}: {len(results)} total")

        except Exception as e:
            logger.error(f"Zillow search failed: {e}")
            raise
        finally:
            await page.close()

        # Deduplicate by zpid/name
        seen: set[str] = set()
        deduped: list[Apartment] = []
        for apt in results:
            key = apt.name.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(apt)

        logger.info(f"Zillow search complete: {len(deduped)} unique listings")
        return deduped


def _parse_zillow_payload(data: dict) -> list[Apartment]:
    """Parse Zillow GetSearchPageState JSON response."""
    results: list[Apartment] = []

    # Zillow response shape: data["cat1"]["searchResults"]["listResults"]
    # or data["cat2"]["searchResults"]["listResults"] (map pins)
    for cat_key in ("cat1", "cat2"):
        cat = data.get(cat_key, {})
        if not isinstance(cat, dict):
            continue
        search_results = cat.get("searchResults", {})
        if not isinstance(search_results, dict):
            continue
        for result_key in ("listResults", "mapResults"):
            items = search_results.get(result_key, [])
            if not isinstance(items, list):
                continue
            for item in items:
                apt = _apt_from_zillow_item(item)
                if apt:
                    results.append(apt)

    return results


def _apt_from_zillow_item(item: dict) -> Apartment | None:
    try:
        name = item.get("buildingName") or item.get("address") or item.get("addressStreet")
        if not name:
            return None

        address_parts = [
            item.get("addressStreet", ""),
            item.get("addressCity", ""),
            item.get("addressState", ""),
            item.get("addressZipcode", ""),
        ]
        address = ", ".join(p for p in address_parts if p) or name

        ll = item.get("latLong", {}) or {}
        lat = float(ll.get("latitude") or item.get("latitude") or 0)
        lon = float(ll.get("longitude") or item.get("longitude") or 0)

        detail_url = item.get("detailUrl", "")
        if detail_url and not detail_url.startswith("http"):
            detail_url = _BASE + detail_url

        # Price: may be a string like "$2,500/mo" or a number
        price_raw = item.get("price") or item.get("unformattedPrice") or ""
        price_min = _parse_price(str(price_raw))

        beds_raw = item.get("beds") or item.get("bedrooms")
        try:
            beds = int(beds_raw) if beds_raw else 2
        except (ValueError, TypeError):
            beds = 2

        baths_raw = item.get("baths") or item.get("bathrooms")
        try:
            baths = float(baths_raw) if baths_raw else 1.0
        except (ValueError, TypeError):
            baths = 1.0

        apt = Apartment(name=name, address=address, lat=lat, lon=lon, website_url=detail_url)
        if price_min:
            apt.units = [Unit(
                floor_plan_name=f"{beds}BR/{baths}BA",
                bed=beds,
                bath=baths,
                price_min=price_min,
                price_max=price_min,
            )]
        return apt
    except Exception:
        return None


def _parse_zillow_html(html: str) -> list[Apartment]:
    """Fallback HTML parser for Zillow listing cards."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    results: list[Apartment] = []

    # Zillow embeds search state in a script tag
    for script in soup.find_all("script", id="__NEXT_DATA__"):
        try:
            data = json.loads(script.string or "")
            # Walk to the search results
            props = data.get("props", {}).get("pageProps", {}).get("searchPageState", {})
            if props:
                results.extend(_parse_zillow_payload(props))
        except (json.JSONDecodeError, AttributeError):
            continue

    if results:
        return results

    # Also try window.__data__ or similar embedded JSON
    for script in soup.find_all("script"):
        text = script.string or ""
        if "listResults" in text or "searchResults" in text:
            m = re.search(r'\{.*"listResults".*\}', text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                    results.extend(_parse_zillow_payload(data))
                    if results:
                        break
                except json.JSONDecodeError:
                    pass

    return results


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
