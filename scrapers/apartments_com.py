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
        # apartments.com bbox order: north,east,south,west
        bb_str = f"{bbox['north']},{bbox['east']},{bbox['south']},{bbox['west']}"
        # Use county slug path — more reliable than generic /apartments/ path
        url = (
            f"{self.BASE}/palm-beach-county-fl/"
            f"?bb={bb_str}"
            f"&min-price={min_price}"
            f"&max-price={max_price}"
            f"&min-beds={min_beds}"
            f"&so=2"  # sort by newest
        )
        logger.info(f"Apartments.com search URL: {url}")

        # Intercept the internal search API response (JSON — much more reliable than HTML parsing)
        api_results: list[dict] = []

        async def _handle_response(response):
            try:
                url_lower = response.url.lower()
                if ("searchresults" in url_lower or "search/results" in url_lower or
                        "listings" in url_lower) and response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        data = await response.json()
                        api_results.append(data)
                        logger.debug(f"Captured API response from {response.url}")
            except Exception:
                pass

        page = await self._new_page()
        page.on("response", _handle_response)

        results: list[Apartment] = []
        try:
            ok = await self._safe_goto(page, url, wait_until="networkidle")
            if not ok:
                raise RuntimeError("Could not load apartments.com search page — possible bot block")

            # Check for bot block page
            title = await page.title()
            logger.info(f"Page title after navigation: {title!r}")
            title_lower = title.lower()
            if any(kw in title_lower for kw in ("access denied", "robot", "captcha", "blocked", "403", "just a moment")):
                raise RuntimeError(f"Bot detection triggered — page title: {title!r}")

            # Also check if we landed on a meaningful page (not a generic redirect)
            current_url = page.url
            logger.info(f"Current URL after navigation: {current_url}")

            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await self._human_delay()
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self._human_delay()

            html = await page.content()
            logger.debug(f"HTML snippet (first 500 chars): {html[:500]}")

            # Try API-intercepted results first
            if api_results:
                logger.info(f"Got {len(api_results)} API response(s), parsing JSON")
                for payload in api_results:
                    results.extend(_parse_api_payload(payload))

            if not results:
                # Fall back to HTML parsing
                logger.info("No API results intercepted, falling back to HTML parsing")
                results = await self._parse_search_results(html)

            logger.info(f"Page 1 results: {len(results)} apartments")

            # Handle pagination (up to 3 pages)
            for page_num in range(2, 4):
                next_sel = f"a[data-page='{page_num}']"
                next_btn = await page.query_selector(next_sel)
                if not next_btn:
                    break
                api_results.clear()
                await next_btn.click()
                await self._human_delay()
                html = await page.content()
                if api_results:
                    for payload in api_results:
                        results.extend(_parse_api_payload(payload))
                else:
                    results.extend(await self._parse_search_results(html))
                logger.info(f"Page {page_num} total results: {len(results)}")

        except Exception as e:
            logger.error(f"Search failed: {e}")
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

        logger.info(f"Search complete: {len(deduped)} unique apartments found")
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


def _parse_api_payload(data: dict) -> list[Apartment]:
    """Parse apartments intercepted from apartments.com internal search API."""
    results: list[Apartment] = []
    # Common shapes: {"placards": [...]} or {"apartments": [...]} or top-level list
    candidates = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        for key in ("placards", "apartments", "listings", "results", "items"):
            if key in data and isinstance(data[key], list):
                candidates = data[key]
                break
        if not candidates:
            # Try nested: {"data": {"apartments": [...]}}
            inner = data.get("data", {})
            if isinstance(inner, dict):
                for key in ("placards", "apartments", "listings", "results"):
                    if key in inner and isinstance(inner[key], list):
                        candidates = inner[key]
                        break

    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("propertyName") or item.get("title")
        if not name:
            continue
        address = item.get("address") or item.get("streetAddress") or ""
        if isinstance(address, dict):
            address = f"{address.get('streetAddress','')}, {address.get('city','')}, {address.get('state','')}".strip(", ")
        lat = float(item.get("latitude") or item.get("lat") or 0)
        lon = float(item.get("longitude") or item.get("lng") or item.get("lon") or 0)
        url = item.get("url") or item.get("propertyUrl") or item.get("detailUrl")
        price_min = None
        for pk in ("minRent", "minPrice", "rentMin", "priceMin", "price"):
            if item.get(pk):
                try:
                    price_min = int(float(item[pk]))
                    break
                except (ValueError, TypeError):
                    pass
        apt = Apartment(name=name, address=address, lat=lat, lon=lon, website_url=url)
        if price_min:
            apt.units = [Unit(floor_plan_name="2BR", bed=2, bath=2.0, price_min=price_min)]
        results.append(apt)

    return results


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
