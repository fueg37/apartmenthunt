"""Base scraper: minimal ABC for API-based scrapers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseScraper(ABC):
    """Thin base class for scrapers. Subclasses use httpx directly."""

    async def close(self) -> None:
        """Release any resources. No-op for stateless HTTP scrapers."""

    @abstractmethod
    async def scrape(self, *args: Any, **kwargs: Any) -> Any:
        ...
