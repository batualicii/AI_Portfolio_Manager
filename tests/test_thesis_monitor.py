"""The monitor: does a written condition actually get checked, and honestly?"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.market.types import Fundamentals
from src.models import Falsifier, FalsifierKind, Market, Thesis
from src.thesis.monitor import ThesisMonitor, fired, unevaluated
from tests.conftest import FakeProvider, _bars

NOW = datetime(2023, 1, 2, tzinfo=timezone.utc)


def _thesis(*falsifiers, entry=100.0) -> Thesis:
    return Thesis(symbol="X", market=Market.US, opened_at=NOW, entry_price=entry,
                  conviction=3, summary="why", falsifiers=falsifiers)


def _provider(closes=None, fundamentals=None) -> FakeProvider:
    hist = {("X", Market.US): _bars(closes)} if closes else {}
    fund = {("X", Market.US): fundamentals} if fundamentals else {}
    return FakeProvider(hist, fund)


# ----------------------------- trend break -------------------------------

def test_a_sustained_break_below_the_average_fires():
    closes = [100.0] * 230 + [60.0] * 25
    checks = ThesisMonitor(_provider(closes)).check(_thesis(
        Falsifier(FalsifierKind.TREND_BREAK, "trend gone", lookback=200, persistence=4)
    ))
    assert checks[0].fired
    assert "the trend is gone" in checks[0].detail


def test_a_drawdown_that_recovers_does_not_fire():
    """The rule must tolerate the drawdowns a position has to survive."""
    closes = [100.0] * 230 + [70.0] * 5 + [115.0] * 20
    checks = ThesisMonitor(_provider(closes)).check(_thesis(
        Falsifier(FalsifierKind.TREND_BREAK, "trend gone", lookback=200, persistence=4)
    ))
    assert not checks[0].fired
    assert "trend intact" in checks[0].detail


def test_too_little_history_reports_unknown_rather_than_fine():
    checks = ThesisMonitor(_provider([100.0] * 50)).check(_thesis(
        Falsifier(FalsifierKind.TREND_BREAK, "trend gone", lookback=200, persistence=4)
    ))
    assert not checks[0].fired
    assert not checks[0].evaluated          # "could not judge" is not "fine"
    assert unevaluated(checks) == checks


def test_no_price_data_at_all_is_also_reported_as_unknown():
    checks = ThesisMonitor(_provider()).check(_thesis(
        Falsifier(FalsifierKind.TREND_BREAK, "trend gone")
    ))
    assert not checks[0].evaluated
    assert "could not check" in checks[0].detail


# ------------------------------ drawdown ---------------------------------

def test_drawdown_measures_from_the_high_since_entry_not_from_the_entry_price():
    closes = [100.0, 150.0, 200.0, 90.0]   # peaked at 200, now 90 => -55%
    checks = ThesisMonitor(_provider(closes)).check(_thesis(
        Falsifier(FalsifierKind.DRAWDOWN, "halved", threshold=0.5)
    ))
    assert checks[0].fired
    assert checks[0].observed == pytest.approx(-55.0, abs=0.1)


def test_a_shallower_fall_than_the_limit_does_not_fire():
    checks = ThesisMonitor(_provider([100.0, 200.0, 140.0])).check(_thesis(
        Falsifier(FalsifierKind.DRAWDOWN, "halved", threshold=0.5)
    ))
    assert not checks[0].fired


# ---------------------------- fundamentals -------------------------------

def test_revenue_growth_below_the_floor_fires_with_the_actual_number():
    fundamentals = Fundamentals(symbol="X", revenue_growth=0.12)
    checks = ThesisMonitor(_provider(fundamentals=fundamentals)).check(_thesis(
        Falsifier(FalsifierKind.REVENUE_GROWTH, "growth stalls", threshold=0.20)
    ))
    assert checks[0].fired
    assert checks[0].observed == pytest.approx(12.0)
    # The alert names the numbers, so it can be acted on rather than felt.
    assert "12.0%" in checks[0].detail and "20.0%" in checks[0].detail


def test_growth_above_the_floor_does_not_fire():
    fundamentals = Fundamentals(symbol="X", revenue_growth=0.31)
    checks = ThesisMonitor(_provider(fundamentals=fundamentals)).check(_thesis(
        Falsifier(FalsifierKind.REVENUE_GROWTH, "growth stalls", threshold=0.20)
    ))
    assert not checks[0].fired


def test_a_missing_fundamental_is_unknown_not_a_pass():
    """BIST coverage is thin; a silent pass there would be actively harmful."""
    checks = ThesisMonitor(_provider(fundamentals=Fundamentals(symbol="X"))).check(
        _thesis(Falsifier(FalsifierKind.PROFIT_MARGIN, "margins go", threshold=0.10))
    )
    assert not checks[0].fired
    assert not checks[0].evaluated


# ------------------------------- manual ----------------------------------

def test_a_manual_falsifier_never_fires_by_itself_but_counts_as_evaluated():
    checks = ThesisMonitor(_provider()).check(_thesis(
        Falsifier(FalsifierKind.MANUAL, "a rival ships at scale")
    ))
    assert not checks[0].fired
    assert checks[0].evaluated              # it is a question, not a gap in the data
    assert "your judgement" in checks[0].detail


def test_several_falsifiers_are_all_checked_and_the_fired_ones_selectable():
    closes = [100.0] * 230 + [60.0] * 25
    fundamentals = Fundamentals(symbol="X", revenue_growth=0.31)
    checks = ThesisMonitor(_provider(closes, fundamentals)).check(_thesis(
        Falsifier(FalsifierKind.TREND_BREAK, "trend", lookback=200, persistence=4),
        Falsifier(FalsifierKind.REVENUE_GROWTH, "growth", threshold=0.20),
        Falsifier(FalsifierKind.MANUAL, "rival"),
    ))
    assert len(checks) == 3
    assert len(fired(checks)) == 1
    assert fired(checks)[0].falsifier.kind is FalsifierKind.TREND_BREAK
