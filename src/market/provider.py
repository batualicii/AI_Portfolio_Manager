"""Abstract data-source interfaces.

Signals, portfolio valuation, and the digest depend ONLY on these protocols, so a
data source (free Yahoo today, a paid BIST feed tomorrow) can be swapped without
touching business logic. This is the dependency-inversion rule from the design notes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.market.types import Bar, Fundamentals, NewsItem, Quote
from src.models import Market


class MarketDataProvider(ABC):
    """Prices, history, and fundamentals for a symbol on a given market."""

    @abstractmethod
    def get_quote(self, symbol: str, market: Market) -> Quote | None:
        """Latest price. None if the symbol can't be priced right now."""

    @abstractmethod
    def get_history(
        self, symbol: str, market: Market, *, period: str = "1y", interval: str = "1d"
    ) -> list[Bar]:
        """Historical OHLCV bars, oldest first. Empty list if unavailable."""

    @abstractmethod
    def get_fundamentals(self, symbol: str, market: Market) -> Fundamentals:
        """Best-effort fundamentals. Always returns an object; fields may be None."""

    @abstractmethod
    def get_fx_rate(self, base: str, quote: str) -> float | None:
        """Spot FX rate so 1 unit of `base` = N units of `quote` (e.g. USD->TRY)."""


class NewsProvider(ABC):
    """Recent news/headlines for a symbol (used for the sentiment signal)."""

    @abstractmethod
    def get_news(
        self, symbol: str, market: Market, *, days: int = 7
    ) -> list[NewsItem]:
        """Recent headlines, newest first. Empty list if none / not supported."""


class NullNewsProvider(NewsProvider):
    """No-op news source — used when no Finnhub key is configured."""

    def get_news(self, symbol: str, market: Market, *, days: int = 7) -> list[NewsItem]:
        return []
