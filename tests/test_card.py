"""The shared card, and the symmetry it exists to enforce."""
from __future__ import annotations

import json

import pytest

from src.market.types import Fundamentals
from src.models import Market
from src.screen.candidates import Candidate
from src.screen.pool import build_pool_cards
from src.storage.db import Store
from src.thesis import card as card_mod
from src.thesis import interpret
from src.thesis.card import build_card
from src.thesis.format import format_pool, format_positions


@pytest.fixture
def stats(tmp_path, monkeypatch):
    path = tmp_path / "sector_stats.json"
    path.write_text(json.dumps({"market": "US", "min_sample": 8, "sectors": {
        "Tech": {
            "revenue_growth": {"median": 0.08, "n": 64, "values": [0.02, 0.08, 0.2]},
            "profit_margin": {"median": 0.05, "n": 64, "values": [0.01, 0.05, 0.1]},
            "pe_ratio": {"median": 30.0, "n": 64, "values": [12.0, 30.0, 55.0]},
        },
    }}))
    monkeypatch.setattr(interpret, "STATS", path)
    return path


class _Price:
    def __init__(self, vs200=12.0, momentum=40.0, worst=-30.0):
        self.vs_200d_pct = vs200
        self.momentum_12_1_pct = momentum
        self.worst_drawdown_pct = worst


FUNDAMENTALS = Fundamentals(symbol="X", sector="Tech", revenue_growth=0.19,
                            profit_margin=0.03, pe_ratio=22.0, beta=1.4)


def test_a_holding_and_a_candidate_are_described_identically(stats, tmp_path):
    """The whole point of one renderer: the pool cannot flatter itself.

    If a candidate reads "strong growth, cheap" while the same numbers in the
    portfolio read "7% of the book", every month the new name looks better than
    the one already owned — and the result is rotation, which is the one thing
    this repo measured as reliably harmful.
    """
    store = Store(tmp_path / "t.db")
    store.save_pool(Market.US, [Candidate(
        "X", "Tech", 1.2e9, 2e6, 0.41, 0.40, True, 0.12, 44.0,
        0.19, 0.03, 22.0, 1.4, -0.30,
    )])
    from_pool = build_pool_cards(store, Market.US)[0]
    from_book = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech")

    assert [f.render() for f in from_pool.facts] == [f.render() for f in from_book.facts]
    assert from_pool.tally == from_book.tally


def test_being_ahead_means_ahead_of_a_named_reference(stats):
    card = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech")
    rendered = {f.label: f.render() for f in card.facts}

    assert "sector +8%, n=64" in rendered["growth"]
    assert card.tally == "3/4 ahead"
    assert {f.label for f in card.ahead} == {"growth", "P/E", "trend"}
    assert {f.label for f in card.behind} == {"margin"}


def test_a_cheap_pe_counts_as_ahead_and_an_expensive_one_does_not(stats):
    """Direction is per metric: more growth is ahead, more P/E is not."""
    dear = Fundamentals(symbol="X", sector="Tech", pe_ratio=55.0)
    cheap = Fundamentals(symbol="X", sector="Tech", pe_ratio=12.0)
    assert build_card("X", Market.US, cheap, sector="Tech").facts[0].ahead is True
    assert build_card("X", Market.US, dear, sector="Tech").facts[0].ahead is False


def test_without_sector_medians_the_card_says_what_it_compared_against(stats):
    """BIST has no peers, so "ahead" must not silently mean something else."""
    f = Fundamentals(symbol="THYAO", sector="Industrials", revenue_growth=0.30)
    card = build_card("THYAO", Market.BIST, f, sector="Industrials")

    reference = card.facts[0].reference
    assert "fixed line" in reference
    assert reference.startswith("fixed line"), "must not read as a peer comparison"
    assert "no sector median" in reference
    assert any("no sector medians" in w for w in card.warnings)


def test_the_tally_is_never_presented_as_a_score():
    """A number between 0 and 10 invites confidence SPEC 6c denies."""
    import inspect

    source = inspect.getsource(card_mod)
    assert "def score" not in source
    card = build_card("X", Market.US, Fundamentals(symbol="X"), sector="Tech")
    assert card.tally == "nothing comparable"


def test_a_position_without_a_thesis_is_flagged_on_its_own_card(stats):
    card = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech",
                      weight=0.08, thesis_summary=None)
    assert card.has_thesis is False
    assert any("no thesis" in w for w in card.warnings)

    owned = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech",
                       weight=0.08, thesis_summary="consolidating market")
    assert owned.has_thesis is True
    assert not any("no thesis" in w for w in owned.warnings)


def test_a_candidate_in_a_sector_you_are_heavy_in_is_flagged_not_hidden(stats):
    """Filtering it out would hide a good name; flagging leaves the call yours."""
    card = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech",
                      crowded_sector_weight=0.32)
    assert any("32% in Tech" in w for w in card.warnings)


def test_positions_output_never_tells_the_owner_to_sell(stats):
    """The rotation rule this repo tested sold NVDA after both of its crashes."""
    card = build_card("X", Market.US, FUNDAMENTALS, _Price(vs200=-9.0),
                      sector="Tech", weight=0.07)
    text = format_positions([card], {"Something changed": [card]}, ["1 position"])

    assert "Nothing here says sell" in text
    lowered = text.lower()
    assert "consider selling" not in lowered
    assert "you should sell" not in lowered


def test_the_pool_states_that_its_order_is_not_a_ranking(stats, tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_pool(Market.US, [Candidate("X", "Tech", 1e9, 2e6, 0.4, 0.4, True,
                                          0.1, 44.0, 0.19, 0.03, 22.0, 1.4, -0.3)])
    text = format_pool(build_pool_cards(store, Market.US), "US", "2026-08-04")

    assert "reading order and nothing else" in text
    assert "not to buy" in text
