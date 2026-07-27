"""Market data value types — plain data returned by providers.

Kept separate from the provider interface so signal/portfolio code can import the
shapes without pulling in yfinance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Quote:
    """A current/last price snapshot for one symbol."""
    symbol: str          # provider symbol actually queried (e.g. THYAO.IS)
    price: float
    currency: str
    prev_close: float | None = None
    as_of: datetime | None = None

    @property
    def day_change_pct(self) -> float | None:
        if self.prev_close in (None, 0):
            return None
        return (self.price - self.prev_close) / self.prev_close * 100.0


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle (daily by default)."""
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Fundamentals:
    """Lightweight fundamentals snapshot. Fields are best-effort; any may be None
    (BIST coverage in particular is thin)."""
    symbol: str
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    profit_margin: float | None = None
    revenue_growth: float | None = None
    beta: float | None = None
    sector: str | None = None
    # What fraction of the shares institutions already hold. The directly
    # relevant number for a small investor: a name large funds have already
    # crowded into offers none of the capacity advantage that makes small-cap
    # investing worth doing at all (SPEC section 0).
    held_pct_institutions: float | None = None


@dataclass(frozen=True)
class NewsItem:
    """A single news headline relevant to a symbol."""
    symbol: str
    headline: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
