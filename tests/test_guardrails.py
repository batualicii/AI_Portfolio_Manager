"""Guardrails: the half of the system that is actually under the owner's control."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import Falsifier, FalsifierKind, Holding, Market, Thesis
from src.portfolio.guardrails import (
    concentration_breaches,
    max_weight_for,
    sale_without_cause,
    trade_pace,
)
from src.portfolio.valuation import PortfolioReport, PositionValue
from src.storage.db import Store

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "g.db")
    yield s
    s.close()


def _thesis(symbol, conviction, market=Market.US, **kw) -> Thesis:
    return Thesis(
        symbol=symbol, market=market, opened_at=kw.pop("opened_at", NOW),
        entry_price=10.0, conviction=conviction, summary="why",
        falsifiers=(Falsifier(FalsifierKind.DRAWDOWN, "halved", threshold=0.5),),
        **kw,
    )


def _report(*positions, usdtry=35.0) -> PortfolioReport:
    values = []
    for symbol, market, value in positions:
        values.append(PositionValue(
            holding=Holding(symbol=symbol, market=market, quantity=1.0,
                            avg_cost=value),
            price=value, day_change_pct=None,
        ))
    return PortfolioReport(positions=values, usdtry=usdtry)


def test_conviction_drives_the_ceiling_and_is_clamped():
    assert max_weight_for(1) < max_weight_for(3) < max_weight_for(5)
    assert max_weight_for(99) == max_weight_for(5)   # nonsense input is clamped
    assert max_weight_for(0) == max_weight_for(1)


def test_a_low_conviction_idea_that_became_the_portfolio_is_flagged(store):
    store.open_thesis(_thesis("SMALL", conviction=1))
    report = _report(("SMALL", Market.US, 50.0), ("OTHER", Market.US, 50.0))

    breaches = concentration_breaches(report, store)
    assert [b.name for b in breaches if b.scope == "position"] == ["SMALL", "OTHER"]
    small = next(b for b in breaches if b.name == "SMALL")
    assert small.actual == pytest.approx(0.5)
    assert small.ceiling == max_weight_for(1)


def test_a_high_conviction_winner_is_told_it_may_be_the_design_working(store):
    store.open_thesis(_thesis("BIG", conviction=5))
    report = _report(("BIG", Market.US, 40.0), ("A", Market.US, 20.0),
                     ("B", Market.US, 20.0), ("C", Market.US, 20.0))

    big = next(b for b in concentration_breaches(report, store) if b.name == "BIG")
    assert "design working" in big.detail
    assert "would not buy it at this weight today" in big.detail


def test_positions_within_their_ceiling_are_not_flagged(store):
    store.open_thesis(_thesis("A", conviction=5))
    store.open_thesis(_thesis("B", conviction=5))
    report = _report(*[(c, Market.US, 10.0) for c in "AB"]
                     + [(str(i), Market.US, 10.0) for i in range(8)])
    assert [b for b in concentration_breaches(report, store)
            if b.name in ("A", "B")] == []


def test_currencies_are_converted_before_weights_are_compared(store):
    """Without conversion a 1000 TRY position would dwarf a 100 USD one."""
    report = _report(("US1", Market.US, 100.0), ("TR1", Market.BIST, 1000.0),
                     usdtry=35.0)
    breaches = concentration_breaches(report, store)
    us = next(b for b in breaches if b.name == "US1")
    # 3500 TRY vs 1000 TRY -> the US position is the larger one.
    assert us.actual > 0.7


def test_no_fx_rate_means_no_concentration_claim(store):
    report = _report(("A", Market.US, 100.0), ("B", Market.BIST, 100.0), usdtry=None)
    assert concentration_breaches(report, store) == []


def test_a_sector_that_became_the_book_is_flagged(store):
    report = _report(("A", Market.US, 50.0), ("B", Market.US, 30.0),
                     ("C", Market.US, 20.0))
    sectors = {"A": "Tech", "B": "Tech", "C": "Health"}
    sector = [b for b in concentration_breaches(report, store, sectors)
              if b.scope == "sector"]
    assert [b.name for b in sector] == ["Tech"]
    assert sector[0].actual == pytest.approx(0.8)


def test_trade_pace_counts_both_directions_and_the_unexplained_ones(store):
    tid = store.open_thesis(_thesis("A", 3))
    store.open_thesis(_thesis("B", 3))
    store.close_thesis(tid, NOW + timedelta(days=1), "panicked", explained=False)

    pace = trade_pace(store, days=90, now=NOW + timedelta(days=2))
    assert (pace.opened, pace.closed, pace.unexplained) == (2, 1, 1)
    assert "1 sold with no falsifier fired" in pace.summary()


def test_a_quiet_quarter_gets_no_lecture(store):
    store.open_thesis(_thesis("A", 3))
    pace = trade_pace(store, days=90, now=NOW + timedelta(days=2))
    assert not pace.brisk
    assert "brisk" not in pace.summary()


def test_a_busy_quarter_says_so(store):
    for i in range(6):
        store.open_thesis(_thesis(f"S{i}", 3))
    pace = trade_pace(store, days=90, now=NOW + timedelta(days=2))
    assert pace.brisk
    assert "under your control" in pace.summary()


def test_selling_after_a_falsifier_fired_needs_no_commentary():
    assert sale_without_cause(fired_count=1, unevaluated_count=0) is None


def test_selling_with_nothing_broken_asks_the_question():
    note = sale_without_cause(fired_count=0, unevaluated_count=0)
    assert note and "thesis broke or the price merely moved" in note


def test_selling_when_checks_could_not_run_says_unknown_not_fine():
    note = sale_without_cause(fired_count=0, unevaluated_count=2)
    assert note and "'unknown' rather than 'fine'" in note
