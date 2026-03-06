"""
Scraper for individual apartment property websites.

Most luxury apartment sites use one of:
  - Entrata CMS  (common for Greystar, Lincoln Properties, etc.)
  - RealPage CMS (common for larger REIT-managed properties)
  - Custom sites

Strategy: generic scraper with CMS-specific overrides.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from models import Apartment, Unit
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

# Known CMS fingerprints (detected from page HTML)
_ENTRATA_SIGNAL = ["entrata", "ResidentPortal", "entratacdn"]
_REALPAGE_SIGNAL = ["realpage", "RPM", "caf.realpage"]


class PropertySiteScraper(BaseScraper):

    async def scrape(self, apartment: Apartment) -> Apartment:
        if not apartment.website_url:
            logger.warning(f"No website_url for {apartment.name}")
            return apartment

        page = await self._new_page()
        try:
            # Try the main site first to detect CMS
            ok = await self._safe_goto(page, apartment.website_url)
            if not ok:
                logger.error(f"Could not load {apartment.website_url}")
                return apartment

            html = await page.content()
            cms = _detect_cms(html)
            logger.info(f"{apartment.name} → CMS: {cms}")

            # Navigate to floor plans / availability page
            fp_url = _find_floor_plans_url(apartment.website_url, html, cms)
            if fp_url and fp_url != apartment.website_url:
                ok = await self._safe_goto(page, fp_url)
                if ok:
                    html = await page.content()

            # Click expand buttons
            for selector in [
                "button:has-text('See All')",
                "button:has-text('View All')",
                "button:has-text('Check Availability')",
                "[data-open-modal]",
            ]:
                try:
                    btns = await page.query_selector_all(selector)
                    for btn in btns[:3]:  # max 3 clicks
                        await btn.click()
                        await self._human_delay()
                except Exception:
                    pass

            html = await page.content()
            units = _extract_units(html, cms)
            if units:
                apartment.units = units
                logger.info(f"{apartment.name}: found {len(units)} units")
            else:
                logger.warning(f"{apartment.name}: no units extracted from property site")

        except Exception as e:
            logger.error(f"Property site scrape failed for {apartment.name}: {e}")
        finally:
            await page.close()

        return apartment


def _detect_cms(html: str) -> str:
    lower = html.lower()
    for signal in _ENTRATA_SIGNAL:
        if signal.lower() in lower:
            return "entrata"
    for signal in _REALPAGE_SIGNAL:
        if signal.lower() in lower:
            return "realpage"
    return "generic"


def _find_floor_plans_url(base_url: str, html: str, cms: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    # Look for links with floor plan / availability keywords
    keywords = ["floor-plan", "floorplan", "floor plan", "availability", "apartments"]
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        if any(k in href or k in text for k in keywords):
            return urljoin(base_url, a["href"])
    # Entrata: typically /floorplans
    if cms == "entrata":
        return urljoin(base_url, "/floorplans")
    # RealPage: typically /apartments
    if cms == "realpage":
        return urljoin(base_url, "/apartments")
    return None


def _extract_units(html: str, cms: str) -> list[Unit]:
    units: list[Unit] = []
    soup = BeautifulSoup(html, "lxml")

    # --- JSON-LD (most stable) ---
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            offers = data.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            for offer in offers:
                u = _unit_from_offer(offer, data.get("name", "Floor Plan"))
                if u:
                    units.append(u)
        except Exception:
            continue

    if units:
        return units

    # --- Entrata-style floor plan cards ---
    if cms == "entrata":
        units.extend(_parse_entrata(soup))
    elif cms == "realpage":
        units.extend(_parse_realpage(soup))
    else:
        units.extend(_parse_generic(soup))

    return units


def _parse_entrata(soup: BeautifulSoup) -> list[Unit]:
    units: list[Unit] = []
    # Entrata typically renders .fp-group or .floor-plan-list items
    for item in soup.select(".fp-group, .floor-plan-item, .fp-unit, [class*='floorplan']"):
        name_el = item.select_one("[class*='name'], [class*='title'], h3, h4")
        name = name_el.get_text(strip=True) if name_el else "Floor Plan"
        text = item.get_text(" ", strip=True)
        price_min, price_max = _parse_price_range(text)
        bed = _parse_beds(text)
        bath = _parse_baths(text)
        sqft = _parse_sqft(text)
        if name and (price_min or bed):
            units.append(Unit(
                floor_plan_name=name, bed=bed or 2, bath=bath or 2.0,
                sqft_min=sqft, price_min=price_min, price_max=price_max, available=True,
            ))
    return units


def _parse_realpage(soup: BeautifulSoup) -> list[Unit]:
    units: list[Unit] = []
    for item in soup.select(".unit-item, .availabilityRow, [class*='unit']"):
        text = item.get_text(" ", strip=True)
        price_min, price_max = _parse_price_range(text)
        bed = _parse_beds(text)
        bath = _parse_baths(text)
        if price_min or bed:
            name_el = item.select_one("[class*='name'], [class*='type'], td:first-child")
            name = name_el.get_text(strip=True) if name_el else "Unit"
            units.append(Unit(
                floor_plan_name=name, bed=bed or 2, bath=bath or 2.0,
                price_min=price_min, price_max=price_max, available=True,
            ))
    return units


def _parse_generic(soup: BeautifulSoup) -> list[Unit]:
    """Best-effort generic extraction."""
    units: list[Unit] = []
    # Look for any element with price + bed patterns
    for el in soup.select("section, article, div[class*='plan'], div[class*='unit'], div[class*='floor']"):
        text = el.get_text(" ", strip=True)
        if len(text) > 500:  # skip huge containers
            continue
        price_min, price_max = _parse_price_range(text)
        bed = _parse_beds(text)
        if price_min and bed:
            name_el = el.select_one("h3, h4, h5, [class*='name'], [class*='title']")
            name = name_el.get_text(strip=True) if name_el else f"{bed}BR Unit"
            units.append(Unit(
                floor_plan_name=name, bed=bed, bath=_parse_baths(text) or 2.0,
                sqft_min=_parse_sqft(text), price_min=price_min, price_max=price_max, available=True,
            ))
    return units


def _unit_from_offer(offer: dict, default_name: str) -> Unit | None:
    try:
        name = offer.get("name", default_name)
        price = offer.get("price") or offer.get("lowPrice")
        price_high = offer.get("highPrice")
        price_val = int(float(price)) if price else None
        price_high_val = int(float(price_high)) if price_high else price_val
        bed = _parse_beds(name) or 2
        bath = _parse_baths(name) or 2.0
        return Unit(
            floor_plan_name=name, bed=bed, bath=bath,
            price_min=price_val, price_max=price_high_val, available=True,
        )
    except Exception:
        return None


def _parse_price_range(text: str) -> tuple[int | None, int | None]:
    prices = re.findall(r"\$[\d,]+", text)
    values = [int(p.replace("$", "").replace(",", "")) for p in prices]
    # Filter out obviously non-rent values (>10k or <500)
    values = [v for v in values if 500 <= v <= 10000]
    if not values:
        return None, None
    return min(values), max(values)


def _parse_beds(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:bd|bed|BR)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_baths(text: str) -> float | None:
    m = re.search(r"(\d+\.?\d*)\s*(?:ba|bath|BA)", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _parse_sqft(text: str) -> int | None:
    m = re.search(r"([\d,]+)\s*(?:sq\.?\s*ft|sqft)", text, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None
