"""Thesis storage: the record that makes SPEC 6b's gate checkable at all."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import Falsifier, FalsifierKind, Market, Thesis
from src.storage.db import Store

NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _thesis(symbol="NVDA", **kw) -> Thesis:
    base = dict(
        symbol=symbol, market=Market.US, opened_at=NOW, entry_price=100.0,
        conviction=4, summary="Owns the compute layer of a platform shift.",
        falsifiers=(
            Falsifier(FalsifierKind.TREND_BREAK, "trend gone", lookback=200,
                      persistence=4),
            Falsifier(FalsifierKind.MANUAL, "a credible rival ships at scale"),
        ),
    )
    base.update(kw)
    return Thesis(**base)


def test_a_thesis_round_trips_with_its_falsifiers(store):
    store.open_thesis(_thesis())
    got = store.get_thesis("NVDA", Market.US)

    assert got is not None
    assert got.summary.startswith("Owns the compute layer")
    assert got.conviction == 4
    assert len(got.falsifiers) == 2
    trend = got.falsifiers[0]
    assert trend.kind is FalsifierKind.TREND_BREAK
    assert (trend.lookback, trend.persistence) == (200, 4)


def test_a_thesis_without_a_checkable_falsifier_is_refused(store):
    """SPEC 6b gate 2 — 'I'll know it when I see it' is not an exit condition."""
    only_manual = _thesis(falsifiers=(
        Falsifier(FalsifierKind.MANUAL, "gut feel"),
    ))
    with pytest.raises(ValueError, match="checkable falsifier"):
        store.open_thesis(only_manual)
    assert store.get_thesis("NVDA", Market.US) is None


def test_opening_a_thesis_writes_an_event(store):
    tid = store.open_thesis(_thesis())
    events = store.thesis_events(tid)
    assert [e["kind"] for e in events] == ["OPENED"]


def test_only_one_open_thesis_per_symbol(store):
    import sqlite3
    store.open_thesis(_thesis())
    with pytest.raises(sqlite3.IntegrityError):
        store.open_thesis(_thesis())


def test_closing_frees_the_symbol_but_keeps_the_history(store):
    tid = store.open_thesis(_thesis())
    store.close_thesis(tid, NOW + timedelta(days=30), "acquired")

    assert store.get_thesis("NVDA", Market.US) is None      # no longer open
    assert len(store.list_theses(include_closed=True)) == 1  # but still recorded
    store.open_thesis(_thesis())                             # and the slot is free
    assert store.get_thesis("NVDA", Market.US) is not None


def test_an_unexplained_sale_is_counted_not_blocked(store):
    """The owner may always sell; the point is that the reason is recorded."""
    tid = store.open_thesis(_thesis())
    store.close_thesis(tid, NOW + timedelta(days=5), "got nervous", explained=False)

    assert store.unexplained_closures() == 1
    detail = store.thesis_events(tid)[0]["detail"]
    assert detail.startswith("UNEXPLAINED")


def test_an_explained_sale_is_not_counted_against_the_owner(store):
    tid = store.open_thesis(_thesis())
    store.close_thesis(tid, NOW + timedelta(days=5), "trend break fired")
    assert store.unexplained_closures() == 0


def test_reviews_move_the_next_due_date(store):
    tid = store.open_thesis(_thesis(review_every_days=90))
    assert store.theses_due_for_review(NOW + timedelta(days=91))

    store.mark_reviewed(tid, NOW + timedelta(days=91), "still holds")
    assert not store.theses_due_for_review(NOW + timedelta(days=100))
    assert store.theses_due_for_review(NOW + timedelta(days=200))


def test_events_are_append_only_and_newest_first(store):
    tid = store.open_thesis(_thesis())
    store.record_thesis_event(tid, "FIRED", "trend break", NOW + timedelta(days=10))
    store.mark_reviewed(tid, NOW + timedelta(days=20))

    kinds = [e["kind"] for e in store.thesis_events(tid)]
    assert kinds == ["REVIEWED", "FIRED", "OPENED"]
