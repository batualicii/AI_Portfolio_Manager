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
