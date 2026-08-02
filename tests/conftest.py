"""Shared fixtures — deterministic market data with no network access.

Every test runs against `FakeProvider`, which implements the same
`MarketDataProvider` interface the app depends on (src/market/provider.py). That
keeps the suite fast and reproducible, and it means the tests exercise the real
dependency-inversion boundary rather than mocking yfinance internals.

The series builders below are fully deterministic — no RNG, not even a seeded
one — so a failing assertion always points at the code, never at the fixture.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.market.provider import MarketDataProvider, NewsProvider
from src.market.types import Bar, Fundamentals, NewsItem, Quote
from src.models import Market

START_DAY = date(2023, 1, 2)  # a Monday; calendar realism doesn't matter here


# ----------------------------- series builders -----------------------------

def _bars(closes: list[float], *, start: date = START_DAY,
          volume: float = 1_000_000.0, spread: float = 0.01) -> list[Bar]:
    """Build OHLCV bars around a close series.

    Open is the previous close (so a jump in `closes` shows up as a gap), and the
    high/low bracket both by `spread`, which keeps synthetic bars self-consistent
    for the stop/target logic in the backtester.
    """
    out: list[Bar] = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        hi = max(open_, close) * (1 + spread)
        lo = min(open_, close) * (1 - spread)
        out.append(Bar(
            day=start + timedelta(days=i),
            open=round(open_, 4), high=round(hi, 4),
            low=round(lo, 4), close=round(close, 4), volume=volume,
        ))
    return out


def uptrend(n: int = 300, start: float = 100.0, step: float = 0.4) -> list[Bar]:
    """Steady linear rise: price above both SMAs, positive momentum.

    Linear (not compounding), so late bars have a smaller percentage change than
    early ones — use `momentum_uptrend` when the test needs a constant rate.
    """
    return _bars([start + step * i for i in range(n)])


def downtrend(n: int = 300, start: float = 220.0, step: float = 0.4) -> list[Bar]:
    """Steady linear decline: price below both SMAs, negative momentum."""
    return _bars([max(start - step * i, 1.0) for i in range(n)])


def momentum_uptrend(n: int = 300, start: float = 100.0,
                     daily_pct: float = 0.007) -> list[Bar]:
    """Compounding rise — a constant ~15% 20-day rate, which saturates the
    momentum term the way a real trending name would."""
    return _bars([start * (1 + daily_pct) ** i for i in range(n)])


def momentum_downtrend(n: int = 300, start: float = 400.0,
                       daily_pct: float = 0.007) -> list[Bar]:
    """Compounding decline, the mirror of `momentum_uptrend`."""
    return _bars([start * (1 - daily_pct) ** i for i in range(n)])


def flat(n: int = 300, price: float = 100.0) -> list[Bar]:
    """Perfectly flat — zero ATR, zero momentum. Exercises divide-by-zero paths."""
    return _bars([price] * n, spread=0.0)


def choppy(n: int = 300, base: float = 100.0, amplitude: float = 5.0) -> list[Bar]:
    """Sawtooth oscillation with no net drift, so trend scores stay near zero."""
    return _bars([base + (amplitude if i % 2 else -amplitude) for i in range(n)])


def uptrend_then_gap_down(n: int = 300, gap_pct: float = 0.30) -> list[Bar]:
    """Rises, then gaps down hard on the final bar.

    The backtester must fill such a bar at the OPEN, not at the stop price — a
    stop cannot execute at a level the market skipped over.
    """
    bars = uptrend(n - 1)
    last = bars[-1]
    gapped = last.close * (1 - gap_pct)
    bars.append(Bar(
        day=last.day + timedelta(days=1),
        open=round(gapped, 4), high=round(gapped, 4),
        low=round(gapped * 0.99, 4), close=round(gapped * 0.995, 4),
        volume=last.volume,
    ))
    return bars


# ------------------------------ fake sources -------------------------------

class FakeProvider(MarketDataProvider):
    """In-memory MarketDataProvider. Keyed by (symbol, market)."""

    def __init__(
        self,
        history: dict[tuple[str, Market], list[Bar]] | None = None,
        fundamentals: dict[tuple[str, Market], Fundamentals] | None = None,
        fx: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._history = history or {}
        self._fundamentals = fundamentals or {}
        self._fx = fx if fx is not None else {("USD", "TRY"): 35.0}
        self.history_calls: list[tuple[str, Market]] = []

    def _key(self, symbol: str, market: Market) -> tuple[str, Market]:
        return (symbol.upper(), market)

    def set_history(self, symbol: str, market: Market, bars: list[Bar]) -> None:
        self._history[self._key(symbol, market)] = bars

    def get_quote(self, symbol: str, market: Market) -> Quote | None:
        bars = self._history.get(self._key(symbol, market))
        if not bars:
            return None
        return Quote(
            symbol=market.yf_symbol(symbol),
            price=bars[-1].close,
            currency=market.currency,
            prev_close=bars[-2].close if len(bars) >= 2 else None,
            as_of=datetime.now(timezone.utc),
        )

    def get_history(
        self, symbol: str, market: Market, *, period: str = "1y", interval: str = "1d"
    ) -> list[Bar]:
        key = self._key(symbol, market)
        self.history_calls.append(key)
        return list(self._history.get(key, []))

    def get_fundamentals(self, symbol: str, market: Market) -> Fundamentals:
        return self._fundamentals.get(
            self._key(symbol, market), Fundamentals(symbol=market.yf_symbol(symbol))
        )

    def get_fx_rate(self, base: str, quote: str) -> float | None:
        if base.upper() == quote.upper():
            return 1.0
        return self._fx.get((base.upper(), quote.upper()))


class FakeNews(NewsProvider):
    """NewsProvider returning a fixed list of headlines for every symbol."""

    def __init__(self, headlines: list[str] | None = None) -> None:
        self._headlines = headlines or []

    def get_news(self, symbol: str, market: Market, *, days: int = 7) -> list[NewsItem]:
        return [
            NewsItem(
                symbol=symbol.upper(), headline=h, url="", source="test",
                published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
            for h in self._headlines
        ]


# --------------------------------- fixtures ---------------------------------

@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def no_news() -> FakeNews:
    return FakeNews()


@pytest.fixture(autouse=True)
def _isolated_sector_stats(tmp_path, monkeypatch):
    """No test may read `universes/sector_stats*.json` from the working tree.

    Those files are built by the reader following USAGE.md step 4, so a suite
    that reads them passes on a fresh clone and fails on a working setup — which
    is what happened: three tests patched `STATS` but not `STATS_DIR`, and once
    the per-market files existed the real data answered instead of the fixture.

    Redirecting both here makes the whole class of bug impossible rather than
    fixing it once per call site. A test that wants stats writes them into its
    own `tmp_path` and patches over this.
    """
    from src.thesis import interpret

    empty = tmp_path / "no-stats-in-the-working-tree"
    monkeypatch.setattr(interpret, "STATS", empty / "sector_stats.json")
    monkeypatch.setattr(interpret, "STATS_DIR", empty)
