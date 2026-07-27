"""The research half: assembling facts, and fitting falsifiers to a name."""
from __future__ import annotations

import pytest

from src.market.types import Fundamentals
from src.models import FalsifierKind, Market
from src.thesis.brief import OPEN_QUESTIONS, build_brief
from src.thesis.suggest import suggest_falsifiers, suggested_command
from tests.conftest import FakeNews, FakeProvider, _bars, momentum_uptrend


def _provider(closes=None, fundamentals=None) -> FakeProvider:
    hist = {("X", Market.US): _bars(closes)} if closes else {}
    fund = {("X", Market.US): fundamentals} if fundamentals else {}
    return FakeProvider(hist, fund)


# --------------------------------- brief ----------------------------------

def test_the_brief_reports_what_it_could_not_find(): 
    """Silence about a gap reads as 'nothing to report' — a different claim."""
    brief = build_brief("X", Market.US, _provider())
    assert brief.missing
    assert any("what the company does" in m for m in brief.missing)
    assert any("price history" in m for m in brief.missing)


def test_the_brief_places_the_price_against_its_own_history():
    closes = [100.0] * 200 + [150.0] * 60
    brief = build_brief("X", Market.US, _provider(closes))
    assert brief.price is not None
    assert brief.price.last == 150.0
    assert brief.price.off_high_pct == pytest.approx(0.0)
    assert brief.price.vs_200d_pct > 0


def test_the_brief_reports_the_worst_fall_the_owner_would_have_sat_through():
    closes = [100.0] * 100 + [40.0] * 100 + [90.0] * 60
    brief = build_brief("X", Market.US, _provider(closes))
    assert brief.price.worst_drawdown_pct == pytest.approx(-60.0, abs=0.5)


def test_the_brief_carries_no_verdict_field():
    """No score, no rating, no recommendation — a ranking cannot be validated
    at this portfolio size, so the structure does not have a place to put one."""
    import dataclasses

    from src.thesis.brief import PriceContext, ResearchBrief

    names = {f.name for f in dataclasses.fields(ResearchBrief)}
    names |= {f.name for f in dataclasses.fields(PriceContext)}
    for forbidden in ("score", "rating", "recommendation", "verdict", "target"):
        assert not any(forbidden in n for n in names), forbidden


def test_the_open_questions_are_the_ones_data_cannot_answer():
    assert len(OPEN_QUESTIONS) >= 4
    assert any("sell" in q.lower() for q in OPEN_QUESTIONS)


# ------------------------------ suggestions -------------------------------

def test_the_drawdown_threshold_comes_from_what_this_name_actually_does():
    """A round 50% means different things for a utility and a biotech."""
    calm = [100.0 + i * 0.1 for i in range(300)]          # barely falls
    wild = ([100.0] * 100 + [45.0] * 100 + [90.0] * 100)  # halves
    calm_dd = next(s for s in suggest_falsifiers("X", Market.US, _provider(calm))
                   if s.falsifier.kind is FalsifierKind.DRAWDOWN)
    wild_dd = next(s for s in suggest_falsifiers("X", Market.US, _provider(wild))
                   if s.falsifier.kind is FalsifierKind.DRAWDOWN)

    assert wild_dd.falsifier.threshold > calm_dd.falsifier.threshold
    assert "worst fall" in wild_dd.basis


def test_every_suggestion_carries_the_observation_behind_it():
    closes = list(momentum_uptrend(400, daily_pct=0.002))
    frame = [b.close for b in closes]
    fundamentals = Fundamentals(symbol="X", revenue_growth=0.40, profit_margin=0.20)
    suggestions = suggest_falsifiers("X", Market.US, _provider(frame, fundamentals))

    assert suggestions
    for s in suggestions:
        assert s.basis and s.spec


def test_growth_and_margin_thresholds_sit_below_today_not_at_it():
    fundamentals = Fundamentals(symbol="X", revenue_growth=0.40, profit_margin=0.20)
    suggestions = suggest_falsifiers(
        "X", Market.US, _provider([100.0] * 300, fundamentals))
    by_kind = {s.falsifier.kind: s.falsifier for s in suggestions}

    assert by_kind[FalsifierKind.REVENUE_GROWTH].threshold < 0.40
    assert by_kind[FalsifierKind.PROFIT_MARGIN].threshold < 0.20


def test_no_fundamentals_still_yields_price_based_falsifiers():
    """BIST coverage is thin; a name with no fundamentals is still monitorable."""
    suggestions = suggest_falsifiers("X", Market.US, _provider([100.0] * 300))
    kinds = {s.falsifier.kind for s in suggestions}
    assert FalsifierKind.TREND_BREAK in kinds
    assert FalsifierKind.DRAWDOWN in kinds


def test_the_suggested_command_is_ready_to_send():
    suggestions = suggest_falsifiers("X", Market.US, _provider([100.0] * 300))
    command = suggested_command("X", Market.US, 45.0, 3, "why I own it", suggestions)

    assert command.startswith("/thesis add US X 45 3 | why I own it | ")
    assert "trend:200/4" in command


def test_a_name_with_no_data_at_all_still_produces_a_usable_line():
    command = suggested_command("X", Market.US, 45.0, 3, "why", [])
    assert "trend:200/4" in command   # never an empty falsifier list
