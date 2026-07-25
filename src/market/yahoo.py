"""Yahoo Finance data provider (free) for US + BIST.

Uses yfinance. BIST symbols are mapped to their `.IS` tickers by Market.yf_symbol.
A small in-process TTL cache avoids re-hitting Yahoo for the same symbol within a
single run (the daily digest prices ~10 names, the backtester many more).

Network/parse failures degrade gracefully: get_quote returns None, get_history
returns [], get_fundamentals returns an all-None object. Callers must handle that.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import yfinance as yf

from src.market.provider import MarketDataProvider
from src.market.types import Bar, Fundamentals, Quote
from src.models import Market
from src.util.cache import TTLCache

log = logging.getLogger(__name__)


class YahooProvider(MarketDataProvider):
    def __init__(self, quote_ttl: float = 300.0) -> None:
        # Quotes cached 5 min; history/fundamentals 1 h (they change slowly intraday).
        self._quote_cache = TTLCache(quote_ttl)
        self._hist_cache = TTLCache(3600.0)
        self._fx_cache = TTLCache(3600.0)

    # ------------------------------ quotes ------------------------------

    def get_quote(self, symbol: str, market: Market) -> Quote | None:
        yf_sym = market.yf_symbol(symbol)
        cached = self._quote_cache.get(yf_sym)
        if isinstance(cached, Quote):
            return cached
        try:
            tk = yf.Ticker(yf_sym)
            hist = tk.history(period="5d", interval="1d")
            if hist.empty:
                log.warning("No quote data for %s", yf_sym)
                return None
            closes = hist["Close"].dropna()
            if closes.empty:
                return None
            price = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
            currency = _safe_currency(tk, market)
            quote = Quote(
                symbol=yf_sym,
                price=price,
                currency=currency,
                prev_close=prev,
                as_of=datetime.now(timezone.utc),
            )
            self._quote_cache.set(yf_sym, quote)
            return quote
        except Exception as exc:  # network, parse, rate-limit — never crash a digest
            log.warning("Quote fetch failed for %s: %s", yf_sym, exc)
            return None

    # ------------------------------ history -----------------------------

    def get_history(
        self, symbol: str, market: Market, *, period: str = "1y", interval: str = "1d"
    ) -> list[Bar]:
        yf_sym = market.yf_symbol(symbol)
        key = f"{yf_sym}:{period}:{interval}"
        cached = self._hist_cache.get(key)
        if isinstance(cached, list):
            return cached
        try:
            hist = yf.Ticker(yf_sym).history(period=period, interval=interval)
            if hist.empty:
                log.warning("No history for %s (%s/%s)", yf_sym, period, interval)
                return []
            bars: list[Bar] = []
            for idx, row in hist.iterrows():
                bars.append(
                    Bar(
                        day=idx.date(),
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row["Volume"]),
                    )
                )
            self._hist_cache.set(key, bars)
            return bars
        except Exception as exc:
            log.warning("History fetch failed for %s: %s", yf_sym, exc)
            return []

    # --------------------------- fundamentals ---------------------------

    def get_fundamentals(self, symbol: str, market: Market) -> Fundamentals:
        yf_sym = market.yf_symbol(symbol)
        try:
            info = yf.Ticker(yf_sym).info or {}
        except Exception as exc:
            log.warning("Fundamentals fetch failed for %s: %s", yf_sym, exc)
            info = {}
        return Fundamentals(
            symbol=yf_sym,
            market_cap=_num(info.get("marketCap")),
            pe_ratio=_num(info.get("trailingPE")),
            forward_pe=_num(info.get("forwardPE")),
            profit_margin=_num(info.get("profitMargins")),
            revenue_growth=_num(info.get("revenueGrowth")),
            beta=_num(info.get("beta")),
            sector=info.get("sector"),
        )

    # ------------------------------- FX ---------------------------------

    def get_fx_rate(self, base: str, quote: str) -> float | None:
        base, quote = base.upper(), quote.upper()
        if base == quote:
            return 1.0
        key = f"{base}{quote}"
        cached = self._fx_cache.get(key)
        if isinstance(cached, float):
            return cached
        try:
            hist = yf.Ticker(f"{base}{quote}=X").history(period="5d", interval="1d")
            closes = hist["Close"].dropna() if not hist.empty else None
            if closes is None or closes.empty:
                return None
            rate = float(closes.iloc[-1])
            self._fx_cache.set(key, rate)
            return rate
        except Exception as exc:
            log.warning("FX fetch failed for %s%s: %s", base, quote, exc)
            return None


# ------------------------------- helpers -------------------------------

def _num(value) -> float | None:
    """Coerce yfinance values to float, treating junk/None as missing."""
    try:
        if value is None:
            return None
        f = float(value)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _safe_currency(tk, market: Market) -> str:
    try:
        cur = tk.fast_info.get("currency")
        if cur:
            return str(cur)
    except Exception:
        pass
    return market.currency
