"""Digest rendering — the only part of the system the user actually reads.

The core-satellite framing (SPEC section 0) is load-bearing: tactical buys and core
risk alerts must stay in separate sections so a BUY idea is never mistaken for
advice to rotate the whole book.
"""
from __future__ import annotations

from src.models import Action, Market, Recommendation
from src.signals.format import SATELLITE_BUDGET_PCT, format_recommendations

HELD = {("AAPL", Market.US)}


def _reco(symbol, action, score=0.5, *, market=Market.US, stop=None, target=None,
          weight=None, rationale="") -> Recommendation:
    return Recommendation(
        symbol=symbol, market=market, action=action, score=score, price=100.0,
        stop_loss=stop, take_profit=target, suggested_weight=weight,
        rationale=rationale,
    )


def test_an_empty_scan_still_renders_both_sections():
    text = format_recommendations([], header="Signal scan")
    assert "Satellite" in text and "Core" in text
    assert "No new tactical entries today." in text
    assert "No holdings tracked yet." in text


def test_the_advisory_disclaimer_is_always_present():
    text = format_recommendations([], header="Signal scan")
    assert "Advisory only" in text


def test_the_satellite_sleeve_states_its_budget():
    text = format_recommendations([], header="Signal scan")
    assert f"{SATELLITE_BUDGET_PCT}%" in text


def test_a_buy_appears_under_satellite_with_its_levels():
    reco = _reco("NVDA", Action.BUY, 0.62, stop=90.0, target=118.0, weight=0.075)
    text = format_recommendations([reco], header="Signal scan", held_keys=HELD)
    assert "NVDA" in text
    assert "stop 90" in text and "target 118" in text and "size 7.5%" in text
    assert text.index("Satellite") < text.index("NVDA") < text.index("Core")


def test_a_breakdown_on_a_held_name_appears_under_core():
    reco = _reco("AAPL", Action.SELL, -0.55)
    text = format_recommendations([reco], header="Signal scan", held_keys=HELD)
    assert "Breakdown alert: AAPL" in text
    assert text.index("Core") < text.index("Breakdown alert")


def test_a_trim_shows_its_protective_stop():
    reco = _reco("AAPL", Action.TRIM, -0.18, stop=95.5)
    text = format_recommendations([reco], header="Signal scan", held_keys=HELD)
    assert "Weakening: AAPL" in text and "stop 95.5" in text


def test_healthy_holds_are_summarised_on_one_line():
    recos = [_reco("AAPL", Action.HOLD, 0.31)]
    text = format_recommendations(recos, header="Signal scan", held_keys=HELD)
    assert "Healthy holds:" in text and "AAPL (+0.31)" in text


def test_sell_and_trim_are_ignored_for_names_that_are_not_held():
    """A SELL only makes sense against a position the user actually owns."""
    recos = [_reco("TSLA", Action.SELL, -0.6), _reco("TSLA", Action.TRIM, -0.2)]
    text = format_recommendations(recos, header="Signal scan", held_keys=HELD)
    assert "TSLA" not in text
    assert "No holdings tracked yet." in text


def test_a_buy_for_a_name_already_held_is_not_offered_again():
    text = format_recommendations(
        [_reco("AAPL", Action.BUY, 0.7)], header="Signal scan", held_keys=HELD
    )
    assert "No new tactical entries today." in text


def test_the_market_and_its_currency_are_shown():
    reco = _reco("THYAO", Action.BUY, 0.5, market=Market.BIST)
    text = format_recommendations([reco], header="Signal scan")
    assert "(BIST)" in text and "TRY" in text


def test_a_very_long_rationale_is_clipped_rather_than_flooding_the_message():
    reco = _reco("NVDA", Action.BUY, 0.6, rationale="x" * 900)
    text = format_recommendations([reco], header="Signal scan")
    assert "…" in text
    assert "x" * 900 not in text


def test_the_header_is_rendered():
    assert "Today's signals" in format_recommendations([], header="Today's signals")
