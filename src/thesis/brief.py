"""Assemble everything knowable about one candidate, so the digging is not manual.

The screen narrows six hundred names to a few dozen. Reading those few dozen was
still entirely the owner's job, which is the part of this system that asked for
more work rather than less. This does the fetching and arranging; the judgement
stays where it has to be.

What it is careful not to be: a verdict. There is no score, no rating and no
recommendation, because SPEC section 6c establishes that a ranking cannot be
shown to work at this portfolio size. Every number here is an observation with
its basis attached, and the questions at the end are the ones a screen cannot
answer — which is precisely where an individual's advantage lives.

**Absent data is reported, never skipped.** BIST fundamentals are thin, and a
brief that quietly omitted what it could not find would read as though the
company had nothing worth noting.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.market.provider import MarketDataProvider, NewsProvider
from src.market.types import Fundamentals, NewsItem
from src.models import Market
from src.signals import indicators as ind


@dataclass(frozen=True)
class PriceContext:
    """Where the price is, relative to its own history — not to a forecast."""
    last: float
    high_52w: float
    low_52w: float
    vs_200d_pct: float | None
    momentum_12_1_pct: float | None
    worst_drawdown_pct: float | None      # deepest peak-to-trough in the window
    annual_vol_pct: float | None
    median_daily_value: float | None

    @property
    def off_high_pct(self) -> float:
        return (self.last / self.high_52w - 1.0) * 100 if self.high_52w else 0.0


@dataclass
class ResearchBrief:
    symbol: str
    market: Market
    fundamentals: Fundamentals
    price: PriceContext | None
    news: list[NewsItem] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _price_context(frame: pd.DataFrame) -> PriceContext | None:
    if frame.empty or len(frame) < 30:
        return None
    close = frame["close"]
    last = float(close.iloc[-1])
    window = close.iloc[-252:] if len(close) >= 252 else close

    avg200 = ind.sma(close, 200)
    vs_200 = (float(last / avg200.iloc[-1] - 1.0) * 100
              if avg200.notna().any() else None)

    momentum = None
    if len(close) >= 252:
        start = float(close.iloc[-252])
        if start > 0:
            momentum = (float(close.iloc[-21]) / start - 1.0) * 100

    running_peak = close.cummax()
    worst = float((close / running_peak - 1.0).min()) * 100

    returns = close.pct_change().dropna()
    vol = float(returns.std() * (252 ** 0.5) * 100) if len(returns) > 30 else None

    value = None
    if "volume" in frame:
        recent = frame.iloc[-60:]
        value = float((recent["close"] * recent["volume"]).median())

    return PriceContext(
        last=last, high_52w=float(window.max()), low_52w=float(window.min()),
        vs_200d_pct=vs_200, momentum_12_1_pct=momentum,
        worst_drawdown_pct=worst, annual_vol_pct=vol, median_daily_value=value,
    )


def build_brief(
    symbol: str, market: Market, provider: MarketDataProvider,
    news: NewsProvider | None = None, period: str = "3y",
) -> ResearchBrief:
    """Gather the facts. Never raises on a thin data source."""
    missing: list[str] = []

    try:
        fundamentals = provider.get_fundamentals(symbol, market)
    except Exception:  # noqa: BLE001
        fundamentals = Fundamentals(symbol=symbol)
        missing.append("fundamentals could not be fetched at all")

    try:
        bars = provider.get_history(symbol, market, period=period)
    except Exception:  # noqa: BLE001
        bars = []
    price = _price_context(ind.bars_to_frame(bars))
    if price is None:
        missing.append("not enough price history to place the current price")

    for label, value in (
        ("what the company does", fundamentals.business_summary),
        ("revenue growth", fundamentals.revenue_growth),
        ("profit margin", fundamentals.profit_margin),
        ("market cap", fundamentals.market_cap),
        ("institutional ownership", fundamentals.held_pct_institutions),
    ):
        if value is None:
            missing.append(label)

    headlines: list[NewsItem] = []
    if news is not None:
        try:
            headlines = news.get_news(symbol, market, days=30)[:5]
        except Exception:  # noqa: BLE001
            missing.append("recent news")

    return ResearchBrief(symbol.upper(), market, fundamentals, price,
                         headlines, missing)


# The questions a screen structurally cannot answer. They are printed with every
# brief because they, not the numbers above them, are where a small investor's
# advantage actually lives — and because a page of figures invites the feeling
# that the work is done.
OPEN_QUESTIONS = (
    "What does this company sell, and to whom — in one sentence, without jargon?",
    "Why is it cheap or unloved right now? If you cannot say, you are guessing.",
    "What has to go right for this to be worth much more in five years?",
    "Who is trying to take this business from them, and why have they not?",
    "What would make you sell — before you own it, while you can still think?",
)
