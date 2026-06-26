"""Finnhub news provider (free tier) for the sentiment signal.

Finnhub's company-news endpoint covers US equities well; BIST coverage is sparse,
so BIST symbols typically return []. Without an API key, construct NullNewsProvider
instead (see provider.py) — the rest of the app treats "no news" as a neutral signal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from src.market.provider import NewsProvider
from src.market.types import NewsItem
from src.models import Market

log = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1/company-news"


class FinnhubNews(NewsProvider):
    def __init__(self, api_key: str, *, timeout: float = 10.0, max_items: int = 15):
        self._key = api_key
        self._timeout = timeout
        self._max_items = max_items

    def get_news(
        self, symbol: str, market: Market, *, days: int = 7
    ) -> list[NewsItem]:
        # Finnhub uses the bare US ticker; BIST isn't well supported but we still try.
        ticker = symbol.upper().replace(".IS", "")
        today = datetime.now(timezone.utc).date()
        frm = (today - timedelta(days=days)).isoformat()
        params = {"symbol": ticker, "from": frm, "to": today.isoformat(),
                  "token": self._key}
        try:
            resp = requests.get(_BASE, params=params, timeout=self._timeout)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as exc:
            log.warning("Finnhub news failed for %s: %s", ticker, exc)
            return []

        if not isinstance(rows, list):
            return []

        items: list[NewsItem] = []
        for r in rows[: self._max_items]:
            headline = (r.get("headline") or "").strip()
            if not headline:
                continue
            ts = r.get("datetime")
            published = (
                datetime.fromtimestamp(ts, tz=timezone.utc)
                if isinstance(ts, (int, float))
                else datetime.now(timezone.utc)
            )
            items.append(
                NewsItem(
                    symbol=ticker,
                    headline=headline,
                    url=r.get("url", ""),
                    source=r.get("source", "Finnhub"),
                    published_at=published,
                    summary=(r.get("summary") or "").strip(),
                )
            )
        items.sort(key=lambda n: n.published_at, reverse=True)
        return items
