"""Keeping a screened pool on hand, so reading it costs nothing.

A sweep of six hundred names is minutes of network calls. A Telegram command
cannot hold that open, and a pool that takes four minutes to appear is a pool
nobody opens. So the scan runs on a schedule, the result is stored, and `/pool`
serves the last one with the date attached.

The stored run is also what makes *"new since last time"* answerable. Without a
previous run to compare against, every opening shows twenty names with nothing
marking which were already there last month — which reads as twenty fresh ideas
and quietly manufactures the urge to act. Comparing runs turns that back into
what it is: mostly the same list, with two or three changes.
"""
from __future__ import annotations

import logging

from src.market.provider import MarketDataProvider
from src.models import Market
from src.screen.candidates import LIMITS, load_universe, screen
from src.thesis.card import build_card

log = logging.getLogger(__name__)


class _Row:
    """A stored pool row, shaped like the Fundamentals the card renderer wants.

    Deliberately not a re-fetch. The figures shown are the ones the filters ran
    against; fetching again would display a different moment than the one that
    produced the list, and the difference would be invisible.
    """

    def __init__(self, row: dict) -> None:
        self.symbol = row["symbol"]
        self.sector = row["sector"]
        self.revenue_growth = row["revenue_growth"]
        self.profit_margin = row["profit_margin"]
        self.pe_ratio = row["pe_ratio"]
        self.beta = row["beta"]
        self.market_cap = None
        self.business_summary = None
        self.held_pct_institutions = row["institutional"]


class _Price:
    """The price facts a card reads, in the shape `PriceContext` provides them.

    Every field a card touches is stored by the screen, so a candidate's trend
    line reads `trend +18% (its own 200-day average)` — character for character
    what a holding's reads. That symmetry is the entire point of `card.py`, and
    it survives only if this class has no gaps the portfolio side does not.
    """

    def __init__(self, row: dict) -> None:
        self.vs_200d_pct = (row["vs_200d"] * 100
                            if row["vs_200d"] is not None else None)
        self.momentum_12_1_pct = (row["momentum"] * 100
                                  if row["momentum"] is not None else None)
        self.worst_drawdown_pct = (row["worst_drawdown"] * 100
                                   if row["worst_drawdown"] is not None else None)


def refresh_pool(
    provider: MarketDataProvider,
    store,
    market: Market,
    top: int = 20,
    max_per_sector: int = 3,
    period: str = "2y",
) -> tuple[int, int]:
    """Run the screen and store the reading list. Returns (kept, stored).

    Only the reading list is stored, not every survivor. The survivors past the
    sector cap were never going to be shown, and keeping them would let a later
    change to the cap silently alter what "new since last time" means.
    """
    symbols, sectors, source = load_universe(market)
    fx = provider.get_fx_rate("USD", "TRY") if market is Market.BIST else 1.0
    if market is Market.BIST and not fx:
        # A fixed TRY size band ages badly under Turkish inflation, so without a
        # rate the size filter is not conservative — it is meaningless.
        raise RuntimeError("no USD/TRY rate; cannot size-filter BIST without one")

    log.info("Screening %s: %d names from %s", market.value, len(symbols), source)
    result = screen(provider, market, symbols, sectors, fx=fx,
                    limits=LIMITS[market], period=period)
    reading = result.reading_list(top, max_per_sector)
    if reading:
        store.save_pool(market, reading)
    return len(result.kept), len(reading)


def build_pool_cards(store, market: Market, sector_weights=None,
                     passed: dict | None = None) -> tuple[list, list]:
    """The stored pool as cards, split into fresh ones and ones already declined.

    Returns `(to_read, already_passed)`. A name you looked at last month and said
    no to is not new information, and showing it in the same list as the rest
    quietly asks you to make the decision again — which is how a research queue
    turns into a treadmill. It is not dropped either: your own reason comes back
    with it, so changing your mind stays possible and stays deliberate.
    """
    sector_weights = sector_weights or {}
    passed = passed or {}
    to_read, declined = [], []

    for row in store.pool(market):
        card = build_card(
            row["symbol"], market, _Row(row), _Price(row),
            sector=row["sector"],
            cap_usd=row["cap_usd"],
            institutional=row["institutional"],
            crowded_sector_weight=sector_weights.get(row["sector"]),
        )
        record = passed.get(row["symbol"])
        if record:
            when = record["decided_at"][:10]
            reason = record["reason"] or "no reason recorded"
            card.warnings.append(f"you passed on this on {when}: {reason}")
            declined.append(card)
        else:
            to_read.append(card)

    return to_read, declined
