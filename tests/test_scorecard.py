"""The selection scorecard — and its refusal to answer early."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.market.types import Bar
from src.models import Market
from src.portfolio.scorecard import MIN_DAYS, MIN_PER_GROUP, build_scorecard
from src.storage.db import Store
from tests.conftest import FakeProvider


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "d.db")


def _provider(prices: dict[str, float]) -> FakeProvider:
    """A provider whose last close is the "today" price for each symbol."""
    history = {
        (symbol, Market.US): [Bar(day=datetime(2024, 1, 1).date(), open=p,
                                  high=p, low=p, close=p, volume=1.0)]
        for symbol, p in prices.items()
    }
    return FakeProvider(history=history)


def _decide(store, symbol, action, price, days_ago):
    store.record_decision(
        symbol, Market.US, action, price, f"{action.lower()} it",
        when=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def test_a_thin_record_reports_that_it_cannot_tell_yet(store):
    """Twelve decisions over eight months produce a number that means nothing."""
    _decide(store, "AAA", "BOUGHT", 100.0, 200)
    _decide(store, "BBB", "PASSED", 100.0, 200)

    card = build_scorecard(store, _provider({"AAA": 200.0, "BBB": 90.0}))

    assert card.ready is False
    assert "Not enough to separate skill from luck" in card.verdict
    # Even a huge apparent lead must not be reported as a finding.
    assert "evidence" not in card.verdict


def test_an_empty_record_asks_for_the_passes_not_the_purchases(store):
    """Purchases alone can only be compared against the index."""
    card = build_scorecard(store, _provider({}))
    assert "/pass" in card.verdict


def test_a_gap_inside_the_noise_is_reported_as_noise(store):
    """The standard error is printed next to the gap, never withheld."""
    for i in range(MIN_PER_GROUP):
        _decide(store, f"B{i}", "BOUGHT", 100.0, MIN_DAYS + 30)
        _decide(store, f"P{i}", "PASSED", 100.0, MIN_DAYS + 30)

    prices = {f"B{i}": 100.0 + (30 if i % 2 else -25) for i in range(MIN_PER_GROUP)}
    prices |= {f"P{i}": 100.0 + (25 if i % 2 else -30) for i in range(MIN_PER_GROUP)}

    card = build_scorecard(store, _provider(prices))

    assert card.ready is True
    assert "standard error" in card.verdict
    assert "inside the noise" in card.verdict


def test_a_gap_beyond_two_standard_errors_is_finally_called_evidence(store):
    """The scorecard is allowed to deliver good news — once it is earned."""
    for i in range(MIN_PER_GROUP * 2):
        _decide(store, f"B{i}", "BOUGHT", 100.0, MIN_DAYS + 30)
        _decide(store, f"P{i}", "PASSED", 100.0, MIN_DAYS + 30)

    # Tight clusters far apart: a real gap with a small standard error.
    prices = {f"B{i}": 150.0 + i * 0.1 for i in range(MIN_PER_GROUP * 2)}
    prices |= {f"P{i}": 100.0 + i * 0.1 for i in range(MIN_PER_GROUP * 2)}

    card = build_scorecard(store, _provider(prices))

    assert card.gap_pp > 40
    assert "first real evidence" in card.verdict


def test_it_is_equally_willing_to_report_a_bad_result(store):
    """Built before there is anything to report, so it cannot be tuned later."""
    for i in range(MIN_PER_GROUP * 2):
        _decide(store, f"B{i}", "BOUGHT", 100.0, MIN_DAYS + 30)
        _decide(store, f"P{i}", "PASSED", 100.0, MIN_DAYS + 30)

    prices = {f"B{i}": 80.0 + i * 0.1 for i in range(MIN_PER_GROUP * 2)}
    prices |= {f"P{i}": 130.0 + i * 0.1 for i in range(MIN_PER_GROUP * 2)}

    card = build_scorecard(store, _provider(prices))

    assert card.gap_pp < 0
    assert "behind the ones you declined" in card.verdict


def test_an_unpriceable_decision_is_listed_rather_than_dropped(store):
    """Dropping them silently biases the comparison toward what is still quoted."""
    _decide(store, "GONE", "PASSED", 100.0, 400)
    _decide(store, "HERE", "BOUGHT", 100.0, 400)

    card = build_scorecard(store, _provider({"HERE": 120.0}))

    assert card.unpriced and "GONE" in card.unpriced[0]
    assert [o.symbol for o in card.bought] == ["HERE"]


def test_opening_a_thesis_records_the_purchase_side(store):
    """Otherwise the owner would have to remember to log every buy by hand."""
    import inspect

    from src.bot import telegram_bot

    source = inspect.getsource(telegram_bot.PortfolioBot._thesis_add)
    assert 'record_decision' in source and '"BOUGHT"' in source


def test_changing_your_mind_is_two_decisions_not_one(store):
    """A name passed in March and bought in August is a second thought."""
    _decide(store, "AAA", "PASSED", 100.0, 300)
    _decide(store, "AAA", "BOUGHT", 130.0, 100)

    assert len(store.decisions()) == 2
    assert store.last_decision("AAA", Market.US)["action"] == "BOUGHT"
    assert "AAA" not in store.passed_symbols(Market.US)
