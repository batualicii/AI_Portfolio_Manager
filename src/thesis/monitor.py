"""Check each thesis against the conditions its owner wrote down.

The mechanism this whole design rests on: the falsifiers were written at
purchase, when there was no pressure. They are checked here, later, when there
may be a great deal of it. The calm version of the owner sets the terms the
anxious version has to argue with.

Two rules that make the difference between a useful alert and a harmful one:

  * **Every verdict carries the observed number.** "Revenue growth 31% -> 12%,
    your threshold was 20%" is a fact the owner can act on. "Fundamentals
    deteriorating" is an adjective, and an adjective is indistinguishable from
    a mood. All numbers here come from deterministic code; the LLM never
    produces one (SPEC section 1).
  * **Not knowing is not the same as fine.** When the data needed to judge a
    condition is missing, the check reports `fired=False` *and says it could not
    be evaluated*. Silently treating an unanswerable question as a pass is how a
    monitoring system lulls the person it is supposed to protect.

Talks only to `MarketDataProvider`, so the whole thing runs offline against
`FakeProvider` in the tests.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.market.provider import MarketDataProvider
from src.models import Falsifier, FalsifierKind, Thesis
from src.signals import indicators as ind

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FalsifierCheck:
    """One condition, and what the data says about it right now."""
    falsifier: Falsifier
    fired: bool
    observed: float | None    # the measured value, None when it could not be read
    detail: str               # deterministic prose, always naming the numbers

    @property
    def evaluated(self) -> bool:
        """False means "could not judge", which is not the same as "fine"."""
        return self.observed is not None or self.falsifier.kind is FalsifierKind.MANUAL


class ThesisMonitor:
    def __init__(self, provider: MarketDataProvider, *, history_period: str = "2y"):
        self._provider = provider
        self._period = history_period

    def check(self, thesis: Thesis) -> list[FalsifierCheck]:
        """Evaluate every falsifier on a thesis. Never raises on bad data."""
        checks: list[FalsifierCheck] = []
        closes = None
        needs_prices = any(
            f.kind in (FalsifierKind.TREND_BREAK, FalsifierKind.DRAWDOWN)
            for f in thesis.falsifiers
        )
        if needs_prices:
            bars = self._provider.get_history(
                thesis.symbol, thesis.market, period=self._period
            )
            frame = ind.bars_to_frame(bars)
            closes = frame["close"] if not frame.empty else None

        fundamentals = None
        if any(f.kind in (FalsifierKind.REVENUE_GROWTH, FalsifierKind.PROFIT_MARGIN)
               for f in thesis.falsifiers):
            fundamentals = self._provider.get_fundamentals(
                thesis.symbol, thesis.market
            )

        for f in thesis.falsifiers:
            checks.append(self._check_one(thesis, f, closes, fundamentals))
        return checks

    def _check_one(self, thesis, f: Falsifier, closes, fundamentals) -> FalsifierCheck:
        if f.kind is FalsifierKind.MANUAL:
            return FalsifierCheck(
                f, False, None,
                f"Needs your judgement — nothing here can decide it: {f.text}",
            )

        if f.kind is FalsifierKind.TREND_BREAK:
            if closes is None or closes.empty:
                return self._unreadable(f, "no price history available")
            ma = f.lookback or 200
            weeks = f.persistence or 4
            verdict = ind.sustained_below_ma(closes, ma, weeks * 5)
            if verdict is None:
                return self._unreadable(
                    f, f"only {len(closes)} bars, need {ma + weeks * 5}")
            last = float(closes.iloc[-1])
            avg = float(ind.sma(closes, ma).iloc[-1])
            gap = (last / avg - 1.0) * 100 if avg > 0 else 0.0
            return FalsifierCheck(
                f, bool(verdict), gap,
                f"price {last:,.2f} vs {ma}-day average {avg:,.2f} ({gap:+.1f}%); "
                + (f"below it every day for {weeks} weeks — the trend is gone"
                   if verdict else f"not {weeks} straight weeks below — trend intact"),
            )

        if f.kind is FalsifierKind.DRAWDOWN:
            if closes is None or closes.empty:
                return self._unreadable(f, "no price history available")
            since = closes.loc[closes.index >= thesis.opened_at.replace(tzinfo=None)] \
                if closes.index.tz is None else closes.loc[closes.index >= thesis.opened_at]
            if since.empty:
                since = closes
            peak = float(since.max())
            last = float(since.iloc[-1])
            fall = (last / peak - 1.0) if peak > 0 else 0.0
            limit = -(f.threshold or 0.5)
            return FalsifierCheck(
                f, fall <= limit, fall * 100,
                f"{fall * 100:+.1f}% from the {peak:,.2f} high since you bought; "
                f"your limit was {limit * 100:.0f}%",
            )

        value, label = None, ""
        if f.kind is FalsifierKind.REVENUE_GROWTH:
            value, label = getattr(fundamentals, "revenue_growth", None), "revenue growth"
        elif f.kind is FalsifierKind.PROFIT_MARGIN:
            value, label = getattr(fundamentals, "profit_margin", None), "profit margin"

        if value is None:
            return self._unreadable(f, f"{label} not reported by the data source")
        limit = f.threshold if f.threshold is not None else 0.0
        return FalsifierCheck(
            f, value < limit, value * 100,
            f"{label} {value * 100:.1f}%, your floor was {limit * 100:.1f}%",
        )

    @staticmethod
    def _unreadable(f: Falsifier, why: str) -> FalsifierCheck:
        # fired=False, but `evaluated` stays False so callers can tell "fine"
        # apart from "unknown". Reporting an unanswerable question as a pass is
        # the failure mode that makes monitoring worse than none.
        return FalsifierCheck(f, False, None, f"could not check — {why}")


def fired(checks: list[FalsifierCheck]) -> list[FalsifierCheck]:
    return [c for c in checks if c.fired]


def unevaluated(checks: list[FalsifierCheck]) -> list[FalsifierCheck]:
    """Conditions the data could not answer. Surfaced, never silently dropped."""
    return [c for c in checks if not c.evaluated]
