"""Reading a number: context without a verdict."""
from __future__ import annotations

from src.market.types import Fundamentals
from src.thesis.interpret import (
    checklist_line,
    quality_checklist,
    read_fundamentals,
)


def test_each_metric_comes_with_a_plain_explanation():
    """The first version printed numbers nobody could read."""
    f = Fundamentals(symbol="X", revenue_growth=0.31, profit_margin=0.12,
                     pe_ratio=24.0)
    readings = read_fundamentals(f)

    assert len(readings) == 3
    for r in readings:
        assert r.meaning, f"{r.label} has no explanation"
        assert r.display


def test_without_peer_data_it_says_so_rather_than_grading():
    """"Expensive" is a forecast wearing a fact's clothes."""
    readings = read_fundamentals(Fundamentals(symbol="X", pe_ratio=24.0))
    assert readings[0].context == "no sector comparison available"


def test_peer_context_names_the_median_and_the_direction(tmp_path, monkeypatch):
    import json

    from src.thesis import interpret

    stats = tmp_path / "sector_stats.json"
    stats.write_text(json.dumps({"sectors": {"Tech": {
        "pe_ratio": {"median": 18.0, "n": 20, "values": [10.0, 18.0, 30.0]},
    }}}))
    monkeypatch.setattr(interpret, "STATS", stats)

    reading = read_fundamentals(Fundamentals(symbol="X", pe_ratio=24.0), "Tech")[0]
    assert "sector median 18" in reading.context
    assert "above it" in reading.context
    assert reading.percentile == 66


def _write_stats(tmp_path, monkeypatch, payload):
    import json

    from src.thesis import interpret

    stats = tmp_path / "sector_stats.json"
    stats.write_text(json.dumps(payload))
    monkeypatch.setattr(interpret, "STATS", stats)
    return stats


def test_a_median_always_states_how_many_names_it_rests_on(tmp_path, monkeypatch):
    """Financials (n=114) and Utilities (n=11) print the same sentence."""
    _write_stats(tmp_path, monkeypatch, {"sectors": {
        "Financials": {"pe_ratio": {"median": 12.0, "n": 114,
                                    "values": [10.0, 12.0, 14.0]}},
        "Utilities": {"pe_ratio": {"median": 20.0, "n": 11,
                                   "values": [18.0, 20.0, 22.0]}},
    }})

    thick = read_fundamentals(Fundamentals(symbol="A", pe_ratio=24.0),
                              "Financials")[0]
    thin = read_fundamentals(Fundamentals(symbol="B", pe_ratio=24.0),
                             "Utilities")[0]

    assert "n=114" in thick.context and not thick.thin
    assert "n=11" in thin.context and thin.thin
    assert "thin" in thin.context, "a fragile median must not read as a firm one"


def test_bist_is_told_why_it_structurally_has_no_peers(tmp_path, monkeypatch):
    """~100 names over ~34 sectors is ~3 each — no re-run fixes that."""
    from src.models import Market
    from src.thesis.interpret import peer_coverage

    _write_stats(tmp_path, monkeypatch,
                 {"market": "US", "min_sample": 8, "sectors": {"Tech": {}}})

    note = peer_coverage("Holding", Market.BIST)
    assert note and "BIST" in note
    assert "not a missing run" in note
    assert "not a stand-in" in note, "US medians must not be offered as a proxy"

    assert peer_coverage("Tech", Market.US) is None


def test_a_sector_dropped_for_thin_coverage_says_so(tmp_path, monkeypatch):
    from src.models import Market
    from src.thesis.interpret import peer_coverage

    _write_stats(tmp_path, monkeypatch, {
        "market": "US", "min_sample": 8, "sectors": {"Tech": {}},
        "omitted": {"Utilities": {"pe_ratio": 5, "beta": 4}},
    })

    note = peer_coverage("Utilities", Market.US)
    assert note and "5 usable names" in note


def test_missing_stats_file_is_reported_as_unbuilt_not_as_nothing_to_say(
        tmp_path, monkeypatch):
    from src.models import Market
    from src.thesis import interpret

    monkeypatch.setattr(interpret, "STATS", tmp_path / "absent.json")
    note = interpret.peer_coverage("Tech", Market.US)
    assert note and "build_sector_stats" in note


def test_the_page_states_that_comparisons_never_cross_sectors():
    """A bank's 24% margin and a retailer's are not the same measurement."""
    from src.thesis.interpret import WITHIN_SECTOR_ONLY

    assert "cannot rank one sector against another" in WITHIN_SECTOR_ONLY


def test_the_checklist_marks_unanswerable_items_apart_from_failures():
    """Missing data is not a failed check — BIST coverage makes this common."""
    checks = quality_checklist(Fundamentals(symbol="X"))
    assert all(c.passed is None for c in checks)
    assert "unanswerable" in checklist_line(checks)


def test_a_healthy_company_passes_the_checks_it_can():
    f = Fundamentals(symbol="X", revenue_growth=0.30, profit_margin=0.20,
                     pe_ratio=22.0)
    checks = quality_checklist(f)
    assert all(c.passed for c in checks)
    assert checklist_line(checks) == "meets 3 of 3 checks"


def test_a_loss_making_shrinking_company_fails_them():
    f = Fundamentals(symbol="X", revenue_growth=-0.10, profit_margin=-0.05,
                     pe_ratio=200.0)
    checks = quality_checklist(f)
    assert not any(c.passed for c in checks)


def test_the_checklist_is_never_reduced_to_a_single_score():
    """A composite number invites confidence this repo measured away."""
    import inspect

    from src.thesis import interpret

    assert "def score" not in inspect.getsource(interpret)
    assert not hasattr(interpret, "overall_score")
