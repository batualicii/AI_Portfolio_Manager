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
    # Pointed at an empty directory so the suite can never pick up a real
    # sector_stats_*.json from the working tree and drift with it.
    monkeypatch.setattr(interpret, "STATS_DIR", tmp_path / "none")
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
    from_pool = build_pool_cards(store, Market.US)[0][0]
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


def test_with_no_medians_at_all_the_card_says_what_it_compared_against(stats):
    """"Ahead" must never silently change meaning between two names."""
    f = Fundamentals(symbol="THYAO", sector="Industrials", revenue_growth=0.30)
    card = build_card("THYAO", Market.BIST, f, sector="Industrials")

    reference = card.facts[0].reference
    assert reference.startswith("fixed line"), "must not read as a peer comparison"
    assert any("fixed lines" in w for w in card.warnings)


def test_a_sector_without_peers_falls_back_to_the_whole_market(tmp_path,
                                                               monkeypatch):
    """BIST: ~100 names over ~34 sectors gives no sector a sample, but the
    index as a whole has one — far better than a fixed line."""
    path = tmp_path / "sector_stats_BIST.json"
    path.write_text(json.dumps({"market": "BIST", "sectors": {}, "overall": {
        "pe_ratio": {"median": 9.0, "n": 96, "values": [6.0, 9.0, 14.0]},
    }}))
    monkeypatch.setattr(interpret, "STATS_DIR", tmp_path)
    monkeypatch.setattr(interpret, "STATS", tmp_path / "absent.json")

    f = Fundamentals(symbol="THYAO", sector="Industrials", pe_ratio=12.0)
    card = build_card("THYAO", Market.BIST, f, sector="Industrials")

    assert "market 9, n=96" in card.facts[0].reference
    assert "sector" not in card.facts[0].reference, "a level is not a peer set"
    assert card.facts[0].ahead is False          # dearer than the market
    # Flagged on the card, spelled out once per report rather than nine times.
    assert card.market_reference is True


def test_each_market_reads_its_own_file(tmp_path, monkeypatch):
    """One shared file meant the second run erased the first, and BIST names
    were then measured against US medians with nothing saying so."""
    (tmp_path / "sector_stats_US.json").write_text(json.dumps(
        {"market": "US", "sectors": {"Tech": {
            "pe_ratio": {"median": 30.0, "n": 64, "values": [30.0]}}}}))
    (tmp_path / "sector_stats_BIST.json").write_text(json.dumps(
        {"market": "BIST", "sectors": {}, "overall": {
            "pe_ratio": {"median": 9.0, "n": 96, "values": [9.0]}}}))
    monkeypatch.setattr(interpret, "STATS_DIR", tmp_path)
    monkeypatch.setattr(interpret, "STATS", tmp_path / "absent.json")

    f = Fundamentals(symbol="X", sector="Tech", pe_ratio=12.0)
    assert "sector 30" in build_card("X", Market.US, f, sector="Tech").facts[0].reference
    assert "market 9" in build_card("X", Market.BIST, f, sector="Tech").facts[0].reference


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
    assert "⚠️ no thesis" in format_positions([card], {"x": [card]}, [])

    owned = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech",
                       weight=0.08, thesis_summary="consolidating market")
    assert owned.has_thesis is True
    assert "⚠️ no thesis" not in format_positions([owned], {"x": [owned]}, [])


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
    to_read, _ = build_pool_cards(store, Market.US)
    text = format_pool(to_read, "US", "2026-08-04")

    assert "reading order and nothing else" in text
    assert "not to buy" in text


def test_a_six_name_median_cannot_look_like_a_hundred_name_one(tmp_path,
                                                               monkeypatch):
    """Every covered BIST sector is n<10 — an "ahead" there is near a coin flip."""
    path = tmp_path / "sector_stats_BIST.json"
    path.write_text(json.dumps({"market": "BIST", "sectors": {
        "Bankacılık": {
            "profit_margin": {"median": 0.20, "n": 6, "values": [0.1, 0.2, 0.3]},
            "pe_ratio": {"median": 5.0, "n": 6, "values": [3.0, 5.0, 8.0]},
        },
    }}))
    monkeypatch.setattr(interpret, "STATS_DIR", tmp_path)
    monkeypatch.setattr(interpret, "STATS", tmp_path / "absent.json")

    f = Fundamentals(symbol="X", sector="Bankacılık", profit_margin=0.25,
                     pe_ratio=4.0)
    card = build_card("X", Market.BIST, f, sector="Bankacılık")

    assert all(fact.thin for fact in card.facts)
    assert all("n=6, thin" in fact.reference for fact in card.facts)
    assert card.tally == "2/2 ahead, 2 on a thin sample"


def test_a_deep_sample_carries_no_thin_marker(stats):
    """n=64 must read as the firmer evidence it is."""
    card = build_card("X", Market.US, FUNDAMENTALS, _Price(), sector="Tech")
    assert not card.thin_facts
    assert "thin" not in card.tally
    assert all("thin" not in f.reference for f in card.facts)


def test_a_position_over_its_ceiling_is_told_how_it_got_there(stats):
    """27.3% of the book is the most important fact in the report, and the
    first version buried it in a percentage nobody weighed."""
    card = build_card("ASTOR", Market.BIST, FUNDAMENTALS, _Price(),
                      sector="Industrials", weight=0.273, since_entry_pct=308.0,
                      ceiling=0.10, value_try=30437)

    assert "27% of the book against a 10% ceiling" in card.tension
    assert "not by being sized there" in card.tension
    assert "30,437 TRY" in " ".join(card.headline), "a decision is about money"


def test_being_down_while_ahead_on_everything_is_named_as_the_question(stats):
    """The tension is the thought; the four numbers alone were not."""
    card = build_card("REGN", Market.US, FUNDAMENTALS, _Price(vs200=8.0),
                      sector="Tech", weight=0.08, since_entry_pct=-24.0,
                      ceiling=0.10)
    # FUNDAMENTALS is behind on margin, so force the all-ahead case.
    strong = Fundamentals(symbol="REGN", sector="Tech", revenue_growth=0.17,
                          profit_margin=0.28, pe_ratio=19.0)
    card = build_card("REGN", Market.US, strong, _Price(vs200=8.0), sector="Tech",
                      weight=0.08, since_entry_pct=-24.0, ceiling=0.10)

    assert card.tally == "4/4 ahead"
    assert "disagree" in card.tension


def test_a_long_downtrend_on_a_cheap_name_is_posed_as_a_question(stats):
    cheap = Fundamentals(symbol="ADBE", sector="Tech", revenue_growth=0.13,
                         profit_margin=0.29, pe_ratio=14.0)
    card = build_card("ADBE", Market.US, cheap, _Price(vs200=-9.0), sector="Tech",
                      weight=0.06, since_entry_pct=-44.0, ceiling=0.10,
                      weeks_below_ma=40)

    assert "40 weeks below" in card.tension
    assert card.tension.endswith("?"), "a question, never an instruction"


def test_the_tension_line_never_becomes_advice(stats):
    """Naming what is in tension is not the same as resolving it."""
    for since, weight, weeks in ((308.0, 0.273, None), (-24.0, 0.08, None),
                                 (-44.0, 0.06, 40), (-29.0, 0.07, 1)):
        card = build_card("X", Market.US, FUNDAMENTALS, _Price(vs200=-2.0),
                          sector="Tech", weight=weight, since_entry_pct=since,
                          ceiling=0.10, weeks_below_ma=weeks)
        lowered = card.tension.lower()
        for word in ("sell", "buy", "trim", "should", "recommend"):
            assert word not in lowered, f"{word!r} in: {card.tension}"


def test_the_market_reference_caveat_is_stated_once_not_once_per_card(stats,
                                                                     tmp_path,
                                                                     monkeypatch):
    """Nine identical paragraphs buried the numbers they existed to qualify."""
    path = tmp_path / "sector_stats_US.json"
    path.write_text(json.dumps({"market": "US", "sectors": {}, "overall": {
        "pe_ratio": {"median": 21.0, "n": 484, "values": [21.0]}}}))
    monkeypatch.setattr(interpret, "STATS_DIR", tmp_path)
    monkeypatch.setattr(interpret, "STATS", tmp_path / "absent.json")

    cards = [build_card(s, Market.US, Fundamentals(symbol=s, sector="Tech",
                                                   pe_ratio=30.0),
                        sector="Tech", weight=0.1) for s in ("A", "B", "C")]
    text = format_positions(cards, {"Nothing has broken": cards}, [])

    assert text.count("mixes industries") == 1


def test_an_impossible_beta_is_dropped_rather_than_printed(stats):
    """Yahoo returns 0.0 for BIST names; that is a missing value, not low beta."""
    f = Fundamentals(symbol="BIMAS", sector="Tech", profit_margin=0.03, beta=0.0)
    assert not any("beta" in a for a in build_card("BIMAS", Market.BIST, f).also)

    real = Fundamentals(symbol="X", sector="Tech", profit_margin=0.03, beta=1.4)
    assert any("beta 1.4" in a for a in build_card("X", Market.US, real).also)
