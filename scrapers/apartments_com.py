"""Scraper for apartments.com — search + individual property detail."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from models import Apartment, Unit
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ApartmentsComScraper(BaseScraper):
    BASE = "https://www.apartments.com"

    async def scrape(self, apartment: Apartment) -> Apartment:
        """Scrape live unit data for a specific apartment property."""
        if not apartment.website_url and not apartment.apartments_com_slug:
            logger.warning(f"No URL or slug for {apartment.name}, skipping")
            return apartment

        page = await self._new_page()
        try:
            # Try apartments.com property page first
            if apartment.apartments_com_slug:
                url = f"{self.BASE}/{apartment.apartments_com_slug}/"
            else:
                url = apartment.website_url

            ok = await self._safe_goto(page, url)
            if not ok:
                logger.error(f"Could not load {url}")
                return apartment

            html = await page.content()
            units = await self._extract_units(page, html)
            apartment.units = units

            # Also try to update rating / review count from the listing
            meta = await self._extract_meta(html)
            if meta.get("rating"):
                apartment.rating = meta["rating"]
            if meta.get("review_count"):
                apartment.review_count = meta["review_count"]

        except Exception as e:
            logger.error(f"Failed scraping {apartment.name}: {e}")
        finally:
            await page.close()

        return apartment

    async def search(
        self,
        bbox: dict,
        min_price: int = 1800,
        max_price: int = 3500,
        min_beds: int = 2,
    ) -> list[Apartment]:
        """Search apartments.com for listings within a bounding box."""
        # Build search URL with bounding box and filters
        bb_str = f"{bbox['north']},{bbox['west']},{bbox['south']},{bbox['east']}"
        url = (
            f"{self.BASE}/apartments/"
            f"?bb={bb_str}"
            f"&min-price={min_price}"
            f"&max-price={max_price}"
            f"&min-beds={min_beds}"
            f"&so=2"  # sort by newest
        )

        page = await self._new_page()
        results: list[Apartment] = []
        try:
            ok = await self._safe_goto(page, url, wait_until="networkidle")
            if not ok:
                logger.error("Could not load apartments.com search page")
                return results

            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await self._human_delay()

            html = await page.content()
            results = await self._parse_search_results(html)

            # Handle pagination (up to 3 pages)
            for page_num in range(2, 4):
                next_sel = f"a[data-page='{page_num}']"
                next_btn = await page.query_selector(next_sel)
                if not next_btn:
                    break
                await next_btn.click()
                await self._human_delay()
                html = await page.content()
                results.extend(await self._parse_search_results(html))

        except Exception as e:
            logger.error(f"Search failed: {e}")
        finally:
            await page.close()

        # Deduplicate by name
        seen: set[str] = set()
        deduped: list[Apartment] = []
        for apt in results:
            if apt.name not in seen:
                seen.add(apt.name)
                deduped.append(apt)
        return deduped

    async def _extract_units(self, page, html: str) -> list[Unit]:
        """Extract floor plans / units from a property page."""
        units: list[Unit] = []

        # --- Strategy 1: JSON-LD schema.org (most stable) ---
        soup = BeautifulSoup(html, "lxml")
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") in ("ApartmentComplex", "Apartment"):
                    offers = data.get("offers", [])
                    if isinstance(offers, dict):
                        offers = [offers]
                    for offer in offers:
                        unit = _unit_from_ld_offer(offer)
                        if unit:
                            units.append(unit)
            except (json.JSONDecodeError, AttributeError):
                continue

        if units:
            logger.debug(f"Extracted {len(units)} units via JSON-LD")
            return units

        # --- Strategy 2: DOM extraction of pricing grid ---
        try:
            # Click "See All" / "View All" buttons to expand floor plans
            for btn_text in ["See All", "View All", "Show All"]:
                btns = await page.query_selector_all(f"button:has-text('{btn_text}')")
                for btn in btns:
                    try:
                        await btn.click()
                        await self._human_delay()
                    except Exception:
                        pass

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            # pricingGridItem is apartments.com's floor plan card
            for item in soup.select(".pricingGridItem, .availabilityInfo"):
                unit = _unit_from_pricing_item(item)
                if unit:
                    units.append(unit)

            # Also try table rows (some properties show a unit table)
            for row in soup.select(".availabilityTable tr:not(:first-child), .unitsList tr"):
                unit = _unit_from_table_row(row)
                if unit:
                    units.append(unit)

        except Exception as e:
            logger.warning(f"DOM extraction failed: {e}")

        logger.debug(f"Extracted {len(units)} units via DOM")
        return units

    async def _extract_meta(self, html: str) -> dict:
        soup = BeautifulSoup(html, "lxml")
        meta: dict[str, Any] = {}
        # Rating from schema or DOM
        rating_el = soup.select_one("[itemprop='ratingValue'], .reviewRating .rating")
        if rating_el:
            try:
                meta["rating"] = float(rating_el.get_text(strip=True))
            except ValueError:
                pass
        count_el = soup.select_one("[itemprop='reviewCount'], .reviewCount")
        if count_el:
            try:
                meta["review_count"] = int(re.sub(r"\D", "", count_el.get_text()))
            except ValueError:
                pass
        return meta

    async def _parse_search_results(self, html: str) -> list[Apartment]:
        """Parse listing cards from a search results page."""
        soup = BeautifulSoup(html, "lxml")
        results: list[Apartment] = []

        # Primary: JSON-LD on search page (search pages embed array of listings)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        apt = _apartment_from_ld(item)
                        if apt:
                            results.append(apt)
                elif isinstance(data, dict) and data.get("@type") == "ItemList":
                    for el in data.get("itemListElement", []):
                        apt = _apartment_from_ld(el.get("item", {}))
                        if apt:
                            results.append(apt)
            except (json.JSONDecodeError, AttributeError):
                continue

        if results:
            return results

        # Fallback: parse listing cards from DOM
        for card in soup.select("article[data-listingid], .placard"):
            apt = _apartment_from_card(card)
            if apt:
                results.append(apt)

        return results


# ─── Parsing helpers ──────────────────────────────────────────────────────────

def _unit_from_ld_offer(offer: dict) -> Unit | None:
    try:
        name = offer.get("name", "Floor Plan")
        price = offer.get("price") or offer.get("lowPrice")
        price_high = offer.get("highPrice")
        price_val = int(float(price)) if price else None
        price_high_val = int(float(price_high)) if price_high else price_val
        # Bed/bath from name heuristic
        bed = _parse_bed_from_text(name)
        bath = _parse_bath_from_text(name)
        return Unit(
            floor_plan_name=name,
            bed=bed or 2,
            bath=bath or 2.0,
            price_min=price_val,
            price_max=price_high_val,
            available=True,
        )
    except Exception:
        return None


def _unit_from_pricing_item(item) -> Unit | None:
    try:
        name_el = item.select_one(".modelName, .floorPlanName, h4, .unitType")
        name = name_el.get_text(strip=True) if name_el else "Floor Plan"

        price_el = item.select_one(".rentLabel, .priceColumn, .pricingColumn")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price_min, price_max = _parse_price_range(price_text)

        detail_text = item.get_text(" ", strip=True)
        bed = _parse_bed_from_text(detail_text)
        bath = _parse_bath_from_text(detail_text)
        sqft = _parse_sqft_from_text(detail_text)

        if not name and not price_min:
            return None
        return Unit(
            floor_plan_name=name,
            bed=bed or 2,
            bath=bath or 2.0,
            sqft_min=sqft,
            price_min=price_min,
            price_max=price_max,
            available=True,
        )
    except Exception:
        return None


def _unit_from_table_row(row) -> Unit | None:
    try:
        cells = row.find_all("td")
        if len(cells) < 2:
            return None
        texts = [c.get_text(strip=True) for c in cells]
        price_min, price_max = _parse_price_range(" ".join(texts))
        bed = _parse_bed_from_text(" ".join(texts))
        if not price_min and not bed:
            return None
        return Unit(
            floor_plan_name=texts[0] if texts else "Unit",
            bed=bed or 2,
            bath=_parse_bath_from_text(" ".join(texts)) or 2.0,
            price_min=price_min,
            price_max=price_max,
            available=True,
        )
    except Exception:
        return None


def _apartment_from_ld(data: dict) -> Apartment | None:
    try:
        if not data.get("name"):
            return None
        geo = data.get("geo", {})
        addr = data.get("address", {})
        address_str = (
            f"{addr.get('streetAddress', '')}, {addr.get('addressLocality', '')}, {addr.get('addressRegion', '')}"
        ).strip(", ")

        lat = float(geo.get("latitude", 0)) if geo.get("latitude") else 0.0
        lon = float(geo.get("longitude", 0)) if geo.get("longitude") else 0.0

        offers = data.get("offers", {})
        price_min = None
        if isinstance(offers, dict):
            p = offers.get("price") or offers.get("lowPrice")
            price_min = int(float(p)) if p else None

        rating = None
        rc = data.get("aggregateRating", {})
        if rc:
            try:
                rating = float(rc.get("ratingValue", 0)) or None
            except (ValueError, TypeError):
                pass

        apt = Apartment(
            name=data["name"],
            address=address_str or data.get("name", ""),
            lat=lat,
            lon=lon,
            website_url=data.get("url"),
            rating=rating,
        )
        if price_min:
            apt.units = [Unit(floor_plan_name="2BR/2BA", bed=2, bath=2.0, price_min=price_min)]
        return apt
    except Exception:
        return None


def _apartment_from_card(card) -> Apartment | None:
    try:
        name_el = card.select_one(".property-title, .js-placardTitle")
        if not name_el:
            return None
        name = name_el.get_text(strip=True)
        addr_el = card.select_one(".property-address, .location")
        address = addr_el.get_text(strip=True) if addr_el else ""
        price_el = card.select_one(".price-range, .rentLabel")
        price_text = price_el.get_text(strip=True) if price_el else ""
        price_min, price_max = _parse_price_range(price_text)
        link_el = card.select_one("a.property-link, a[href*='apartments.com']")
        url = link_el["href"] if link_el and link_el.get("href") else None

        apt = Apartment(name=name, address=address, lat=0.0, lon=0.0, website_url=url)
        if price_min:
            apt.units = [Unit(floor_plan_name="2BR", bed=2, bath=2.0, price_min=price_min, price_max=price_max)]
        return apt
    except Exception:
        return None


def _parse_price_range(text: str) -> tuple[int | None, int | None]:
    prices = re.findall(r"\$[\d,]+", text)
    values = [int(p.replace("$", "").replace(",", "")) for p in prices if p]
    if not values:
        return None, None
    return values[0], values[-1] if len(values) > 1 else values[0]


def _parse_bed_from_text(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(?:bd|bed|BR)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_bath_from_text(text: str) -> float | None:
    m = re.search(r"(\d+\.?\d*)\s*(?:ba|bath|BA)", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _parse_sqft_from_text(text: str) -> int | None:
    m = re.search(r"([\d,]+)\s*(?:sq\.?\s*ft|sqft)", text, re.IGNORECASE)
    return int(m.group(1).replace(",", "")) if m else None
