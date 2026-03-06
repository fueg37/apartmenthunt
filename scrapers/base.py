"""Base scraper: async playwright setup, anti-detection, retry logic."""
from __future__ import annotations

import asyncio
import random
import logging
from abc import ABC, abstractmethod
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

from config import settings

logger = logging.getLogger(__name__)

# Realistic user agents to rotate
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]


class BaseScraper(ABC):
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=random.choice(_USER_AGENTS),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            },
        )
        # Patch navigator.webdriver to avoid detection
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    async def _new_page(self) -> Page:
        await self._ensure_browser()
        assert self._context is not None
        page = await self._context.new_page()
        return page

    async def _human_delay(self) -> None:
        delay = random.uniform(settings.scrape_delay_min, settings.scrape_delay_max)
        await asyncio.sleep(delay)

    async def _safe_goto(self, page: Page, url: str, wait_until: str = "domcontentloaded") -> bool:
        """Navigate with retry and error handling. Returns True on success."""
        for attempt in range(3):
            try:
                await page.goto(url, wait_until=wait_until, timeout=30_000)
                await self._human_delay()
                return True
            except Exception as e:
                logger.warning(f"Navigation attempt {attempt + 1} failed for {url}: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt * 2)
        return False

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        self._browser = None
        self._context = None
        self._pw = None

    @abstractmethod
    async def scrape(self, *args: Any, **kwargs: Any) -> Any:
        ...
