"""SQLite store — holdings sync and the recommendation audit trail.

The audit trail is a SPEC section 6b requirement: every call the system makes has to
be recoverable later so real-world performance can be reviewed honestly. That
means round-tripping must be lossless, including the optional level fields.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import Action, Holding, Market, Recommendation
from src.storage.db import Store


@pytest.fixture
def store(tmp_path) -> Store:
    s = Store(tmp_path / "nested" / "portfolio.db")
    yield s
    s.close()


def _reco(symbol: str = "AAPL", action: Action = Action.BUY, **kw) -> Recommendation:
    fields = dict(
        symbol=symbol, market=Market.US, action=action, score=0.55, price=190.25,
        stop_loss=180.0, take_profit=210.0, suggested_weight=0.08,
        rationale="momentum + trend", created_at=datetime.now(timezone.utc),
    )
    fields.update(kw)
    return Recommendation(**fields)


# -------------------------------- holdings ---------------------------------

def test_the_database_directory_is_created_on_demand(tmp_path):
    path = tmp_path / "does" / "not" / "exist" / "p.db"
    Store(path).close()
    assert path.exists()


def test_a_holding_round_trips(store):
    store.upsert_holding(Holding("AAPL", Market.US, 10, 185.5, note="core"))
    (held,) = store.list_holdings()
    assert (held.symbol, held.market, held.quantity, held.avg_cost, held.note) == (
        "AAPL", Market.US, 10, 185.5, "core",
    )


def test_upserting_the_same_symbol_updates_instead_of_duplicating(store):
    store.upsert_holding(Holding("AAPL", Market.US, 10, 185.5))
    store.upsert_holding(Holding("AAPL", Market.US, 25, 190.0, note="added"))
    (held,) = store.list_holdings()
    assert held.quantity == 25 and held.avg_cost == 190.0 and held.note == "added"


def test_symbols_are_normalised_to_upper_case(store):
    store.upsert_holding(Holding("aapl", Market.US, 1, 1.0))
    assert store.list_holdings()[0].symbol == "AAPL"


def test_the_same_symbol_on_two_markets_is_two_positions(store):
    store.upsert_holding(Holding("XYZ", Market.US, 1, 1.0))
    store.upsert_holding(Holding("XYZ", Market.BIST, 2, 2.0))
    assert len(store.list_holdings()) == 2


def test_removing_a_holding_reports_whether_anything_was_deleted(store):
    store.upsert_holding(Holding("AAPL", Market.US, 10, 185.5))
    assert store.remove_holding("aapl", Market.US) is True
    assert store.remove_holding("AAPL", Market.US) is False
    assert store.list_holdings() == []


def test_removing_only_affects_the_named_market(store):
    store.upsert_holding(Holding("XYZ", Market.US, 1, 1.0))
    store.upsert_holding(Holding("XYZ", Market.BIST, 2, 2.0))
    store.remove_holding("XYZ", Market.US)
    (left,) = store.list_holdings()
    assert left.market is Market.BIST


# ----------------------------- recommendations -----------------------------

def test_a_recommendation_round_trips_with_all_levels(store):
    original = _reco()
    store.log_recommendation(original)
    (loaded,) = store.recent_recommendations()
    assert loaded.symbol == original.symbol
    assert loaded.action is original.action
    assert loaded.score == pytest.approx(original.score)
    assert loaded.price == pytest.approx(original.price)
    assert loaded.stop_loss == pytest.approx(original.stop_loss)
    assert loaded.take_profit == pytest.approx(original.take_profit)
    assert loaded.suggested_weight == pytest.approx(original.suggested_weight)
    assert loaded.rationale == original.rationale


def test_a_sell_round_trips_with_null_levels(store):
    store.log_recommendation(
        _reco(action=Action.SELL, stop_loss=None, take_profit=None,
              suggested_weight=None)
    )
    (loaded,) = store.recent_recommendations()
    assert loaded.action is Action.SELL
    assert loaded.stop_loss is None
    assert loaded.take_profit is None
    assert loaded.suggested_weight is None


def test_the_log_is_append_only_and_returns_newest_first(store):
    base = datetime(2024, 5, 1, tzinfo=timezone.utc)
    for i, symbol in enumerate(["OLD", "MID", "NEW"]):
        store.log_recommendation(_reco(symbol, created_at=base + timedelta(days=i)))
    assert [r.symbol for r in store.recent_recommendations()] == ["NEW", "MID", "OLD"]


def test_the_log_respects_its_limit(store):
    for i in range(10):
        store.log_recommendation(_reco(f"S{i}"))
    assert len(store.recent_recommendations(limit=3)) == 3


def test_logging_returns_a_usable_row_id(store):
    first = store.log_recommendation(_reco("AAA"))
    second = store.log_recommendation(_reco("BBB"))
    assert isinstance(first, int) and second > first
