"""Craigslist apartment scraper — easier to access than apartments.com."""
from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from models import Apartment, Unit
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# South Florida Craigslist covers Palm Beach County
_CL_BASE = "https://miami.craigslist.org"


class CraigslistScraper(BaseScraper):

    async def scrape(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def search(
        self,
        lat: float = 26.46,
        lon: float = -80.07,
        min_price: int = 1800,
        max_price: int = 3500,
        min_beds: int = 2,
        radius_miles: int = 20,
    ) -> list[Apartment]:
        """Search Craigslist South Florida for apartments near Palm Beach County."""
        url = (
            f"{_CL_BASE}/search/apa"
            f"?sort=date"
            f"&min_price={min_price}"
            f"&max_price={max_price}"
            f"&min_bedrooms={min_beds}"
            f"&search_distance={radius_miles}"
            f"&lat={lat}"
            f"&lon={lon}"
        )
        logger.info(f"Craigslist search URL: {url}")

        page = await self._new_page()
        results: list[Apartment] = []

        try:
            ok = await self._safe_goto(page, url, wait_until="domcontentloaded")
            if not ok:
                raise RuntimeError("Could not load Craigslist search page")

            title = await page.title()
            logger.info(f"Craigslist page title: {title!r}")

            # Scroll to load more listings
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self._human_delay()

            html = await page.content()
            results = _parse_craigslist_results(html)
            logger.info(f"Craigslist page 1: {len(results)} listings")

            # Fetch up to 2 more pages via the "next" button
            for _ in range(2):
                next_btn = await page.query_selector("a.cl-next-page, button[title='next page'], a[title='next']")
                if not next_btn:
                    break
                await next_btn.click()
                await self._human_delay()
                html = await page.content()
                page_results = _parse_craigslist_results(html)
                results.extend(page_results)
                logger.info(f"Craigslist next page: +{len(page_results)} listings ({len(results)} total)")
                if not page_results:
                    break

        except Exception as e:
            logger.error(f"Craigslist search failed: {e}")
            raise
        finally:
            await page.close()

        # Deduplicate by name
        seen: set[str] = set()
        deduped: list[Apartment] = []
        for apt in results:
            if apt.name not in seen:
                seen.add(apt.name)
                deduped.append(apt)

        logger.info(f"Craigslist search complete: {len(deduped)} unique listings")
        return deduped


def _parse_craigslist_results(html: str) -> list[Apartment]:
    soup = BeautifulSoup(html, "lxml")
    results: list[Apartment] = []

    # New Craigslist layout (2023+): .cl-static-search-result or li[data-pid]
    for item in soup.select("li.cl-static-search-result, li[data-pid]"):
        apt = _parse_new_listing(item)
        if apt:
            results.append(apt)

    if results:
        return results

    # Fallback: old layout .result-row
    for row in soup.select(".result-row"):
        apt = _parse_old_listing(row)
        if apt:
            results.append(apt)

    return results


def _parse_new_listing(item) -> Apartment | None:
    try:
        title_el = item.select_one("a.cl-app-anchor, .title")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        link = title_el.get("href", "")
        if link and not link.startswith("http"):
            link = _CL_BASE + link

        price_el = item.select_one(".priceinfo, .price")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = _parse_price(price_text)

        meta_el = item.select_one(".meta, .housing")
        meta_text = meta_el.get_text(" ", strip=True) if meta_el else ""
        beds = _parse_beds(meta_text)
        if beds is not None and beds < 2:
            return None  # Filter out studios/1BR at parse time

        hood_el = item.select_one(".location, .hood, [class*='location']")
        address = hood_el.get_text(strip=True).strip("() ") if hood_el else ""

        # Try to get lat/lon from data attribute
        lat = float(item.get("data-latitude") or 0)
        lon = float(item.get("data-longitude") or 0)

        apt = Apartment(name=title, address=address, lat=lat, lon=lon, website_url=link)
        if price:
            apt.units = [Unit(
                floor_plan_name=f"{beds or 2}BR",
                bed=beds or 2,
                bath=1.0,
                price_min=price,
                price_max=price,
            )]
        return apt
    except Exception:
        return None


def _parse_old_listing(row) -> Apartment | None:
    try:
        title_el = row.select_one(".result-title, a.hdrlnk")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        link = title_el.get("href", "")
        if link and not link.startswith("http"):
            link = _CL_BASE + link

        price_el = row.select_one(".result-price")
        price = _parse_price(price_el.get_text(strip=True) if price_el else "")

        housing_el = row.select_one(".housing")
        housing_text = housing_el.get_text(strip=True) if housing_el else ""
        beds = _parse_beds(housing_text)

        hood_el = row.select_one(".result-hood")
        address = hood_el.get_text(strip=True).strip("() ") if hood_el else ""

        apt = Apartment(name=title, address=address, lat=0.0, lon=0.0, website_url=link)
        if price:
            apt.units = [Unit(
                floor_plan_name=f"{beds or 2}BR",
                bed=beds or 2,
                bath=1.0,
                price_min=price,
                price_max=price,
            )]
        return apt
    except Exception:
        return None


def _parse_price(text: str) -> int | None:
    m = re.search(r"\$[\d,]+", text)
    if not m:
        return None
    try:
        return int(m.group().replace("$", "").replace(",", ""))
    except ValueError:
        return None


def _parse_beds(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:br|bd|bed)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None
