"""The limits the owner set for themselves, enforced when it is inconvenient.

Two generations of evidence in this repo say the signal contributes little and
cannot be proven at all (SPEC section 6c). What *is* well established, and cheap
to act on, is the other half: Barber and Odean's finding that individual
investors' returns fall the more they trade. So this module measures the things
that are actually under the owner's control.

Nothing here blocks anything. It is an advisory bot and the owner may always do
as they like — the point is that a decision made under pressure gets compared,
out loud, with the one made calmly. Every check reports the observed number so
the comparison is a fact rather than a mood.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.models import Market
from src.portfolio.valuation import PortfolioReport
from src.storage.db import Store

# Conviction 1-5 -> the largest share of the whole portfolio that position may
# take. Deliberately generous at the top: this design exists to allow
# concentration (SPEC section 0), and a ceiling so tight that no position can
# ever matter would defeat it. The floor matters more than the ceiling — it is
# what keeps a low-conviction idea from quietly becoming the portfolio.
CONVICTION_CEILING = {1: 0.03, 2: 0.06, 3: 0.10, 4: 0.15, 5: 0.25}

# Above these, one bad outcome stops being a setback.
SECTOR_CEILING = 0.40
MARKET_CEILING = 0.85

# Trades per quarter in the concentrated sleeve. Not a rule of nature — a line
# drawn where a multi-year holding horizon stops being one.
TRADE_PACE_WARN = 4


@dataclass(frozen=True)
class Breach:
    """One limit that is currently exceeded, with the numbers behind it."""
    scope: str          # "position" | "sector" | "market"
    name: str
    actual: float       # observed share of the portfolio, 0-1
    ceiling: float
    detail: str

    @property
    def excess_pct(self) -> float:
        return (self.actual - self.ceiling) * 100


def max_weight_for(conviction: int) -> float:
    """The position ceiling implied by a conviction level."""
    return CONVICTION_CEILING.get(max(1, min(5, int(conviction))), 0.10)


def _weights_try(report: PortfolioReport) -> tuple[dict[tuple[str, Market], float], float]:
    """Each position's share of the whole book, in one currency.

    Mixing a USD sleeve and a TRY sleeve without converting would make every US
    position look small and every BIST position look large, so concentration
    limits are computed on a single converted total or not at all.
    """
    rate = report.usdtry
    if rate in (None, 0):
        return {}, 0.0
    values: dict[tuple[str, Market], float] = {}
    total = 0.0
    for p in report.positions:
        mv = p.market_value
        if mv is None:
            continue
        in_try = mv * rate if p.holding.market is Market.US else mv
        values[(p.holding.symbol.upper(), p.holding.market)] = in_try
        total += in_try
    return values, total


def concentration_breaches(
    report: PortfolioReport, store: Store, sectors: dict[str, str] | None = None
) -> list[Breach]:
    """Positions, sectors and markets that have grown past their limit.

    A position that grew past its ceiling by *winning* is the good problem, and
    the report says so rather than implying a sale: this design is built to let
    winners run (SPEC section 0), and trimming on sight would undo exactly the
    behaviour the exit-rule work argued for.
    """
    values, total = _weights_try(report)
    if not values or total <= 0:
        return []

    breaches: list[Breach] = []
    theses = {(t.symbol.upper(), t.market): t for t in store.list_theses()}

    for key, value in values.items():
        share = value / total
        thesis = theses.get(key)
        ceiling = max_weight_for(thesis.conviction) if thesis else CONVICTION_CEILING[3]
        if share > ceiling:
            grew_into_it = thesis is not None and share > ceiling * 1.2
            breaches.append(Breach(
                "position", key[0], share, ceiling,
                f"{share * 100:.1f}% of the book against a {ceiling * 100:.0f}% "
                f"ceiling for conviction {thesis.conviction if thesis else 3}"
                + (" — if it got there by rising, that is the design working; "
                   "trim only if you would not buy it at this weight today"
                   if grew_into_it else ""),
            ))

    if sectors:
        by_sector: dict[str, float] = {}
        for (symbol, _), value in values.items():
            by_sector[sectors.get(symbol, "Unknown")] = (
                by_sector.get(sectors.get(symbol, "Unknown"), 0.0) + value
            )
        for sector, value in by_sector.items():
            share = value / total
            if share > SECTOR_CEILING:
                breaches.append(Breach(
                    "sector", sector, share, SECTOR_CEILING,
                    f"{share * 100:.1f}% of the book is {sector} — one industry's "
                    f"bad year would be the whole portfolio's",
                ))

    for market in (Market.US, Market.BIST):
        value = sum(v for (_, m), v in values.items() if m is market)
        share = value / total
        if share > MARKET_CEILING:
            breaches.append(Breach(
                "market", market.value, share, MARKET_CEILING,
                f"{share * 100:.1f}% sits in {market.value}; the currency and the "
                f"market are then the same bet",
            ))

    return sorted(breaches, key=lambda b: b.excess_pct, reverse=True)


@dataclass(frozen=True)
class TradePace:
    opened: int
    closed: int
    unexplained: int
    days: int

    @property
    def trades(self) -> int:
        return self.opened + self.closed

    @property
    def brisk(self) -> bool:
        return self.trades > TRADE_PACE_WARN

    def summary(self) -> str:
        line = (f"{self.trades} trades in {self.days} days "
                f"({self.opened} opened, {self.closed} closed)")
        if self.unexplained:
            line += f", {self.unexplained} sold with no falsifier fired"
        if self.brisk:
            line += (
                "\nThat is a brisk pace for a multi-year horizon. The best-"
                "established finding in retail investing is that returns fall as "
                "trading rises — it is the one part of this whole system that is "
                "actually under your control."
            )
        return line


def trade_pace(store: Store, days: int = 90, now: datetime | None = None) -> TradePace:
    """How much trading has happened lately, and how much of it was justified."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    opened = closed = 0
    for thesis in store.list_theses(include_closed=True):
        if thesis.opened_at >= since:
            opened += 1
        if thesis.closed_at is not None and thesis.closed_at >= since:
            closed += 1
    return TradePace(opened, closed, store.unexplained_closures(since), days)


def sale_without_cause(fired_count: int, unevaluated_count: int) -> str | None:
    """What to say when a position is being sold and nothing has broken.

    Returns None when a falsifier did fire — then the sale is the system working
    as designed and needs no commentary.
    """
    if fired_count:
        return None
    if unevaluated_count:
        return (
            "No falsifier fired — but "
            f"{unevaluated_count} could not be checked, so this is 'unknown' "
            "rather than 'fine'. Worth reading them before you decide."
        )
    return (
        "No falsifier fired. Everything you wrote down at purchase still holds, "
        "so the question is whether your thesis broke or the price merely moved. "
        "Selling anyway is your call and will be recorded as unexplained — that "
        "count is the honest measure of whether this system is worth running."
    )
