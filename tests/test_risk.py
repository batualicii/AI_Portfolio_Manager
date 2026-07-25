"""Position sizing and level placement — the guardrails that bound a bad trade.

SPEC section 1 requires a mandatory stop on every entry and a hard cap on any single
position. Both are asserted here, along with the volatility damping that stops a
wide-ATR name from turning a 2-ATR stop into an oversized monetary loss.
"""
from __future__ import annotations

import pytest

from src.signals.config import SignalConfig
from src.signals.risk import buy_levels, hold_stop, stop_breached
from src.signals.scoring import TechnicalView

CFG = SignalConfig()


def test_stop_and_target_sit_at_the_configured_atr_multiples():
    levels = buy_levels(100.0, 2.0, CFG.buy_threshold, CFG, risk_off=False)
    assert levels.stop_loss == pytest.approx(100.0 - CFG.atr_stop_mult * 2.0)
    assert levels.take_profit == pytest.approx(100.0 + CFG.atr_target_mult * 2.0)


def test_reward_to_risk_matches_the_configured_multiples():
    levels = buy_levels(100.0, 2.0, 0.5, CFG, risk_off=False)
    expected = CFG.atr_target_mult / CFG.atr_stop_mult
    assert levels.reward_risk == pytest.approx(expected, rel=1e-3)


def test_every_buy_gets_a_stop_below_the_entry():
    # SPEC section 1: no entry without a stop.
    for conviction in (CFG.buy_threshold, 0.6, 1.0):
        levels = buy_levels(50.0, 1.0, conviction, CFG, risk_off=False)
        assert levels.stop_loss is not None
        assert 0 < levels.stop_loss < 50.0


def test_a_stop_can_never_be_pushed_below_zero_by_a_huge_atr():
    # A pathological ATR must not produce a negative (nonsensical) stop.
    levels = buy_levels(10.0, 500.0, 1.0, CFG, risk_off=False)
    assert levels.stop_loss > 0


def test_a_just_qualifying_buy_gets_the_baseline_weight():
    levels = buy_levels(100.0, 1.0, CFG.buy_threshold, CFG, risk_off=False)
    assert levels.suggested_weight == pytest.approx(CFG.base_position_weight, abs=1e-4)


def test_maximum_conviction_is_capped_at_the_per_name_limit():
    levels = buy_levels(100.0, 1.0, 1.0, CFG, risk_off=False)
    assert levels.suggested_weight == pytest.approx(CFG.max_position_weight, abs=1e-4)


def test_weight_never_exceeds_the_hard_cap_even_above_full_conviction():
    levels = buy_levels(100.0, 0.01, 5.0, CFG, risk_off=False)
    assert levels.suggested_weight <= CFG.max_position_weight


def test_higher_conviction_earns_a_larger_position():
    low = buy_levels(100.0, 1.0, 0.45, CFG, risk_off=False)
    high = buy_levels(100.0, 1.0, 0.9, CFG, risk_off=False)
    assert high.suggested_weight > low.suggested_weight


def test_a_volatile_name_is_trimmed_relative_to_a_calm_one():
    calm = buy_levels(100.0, 1.0, 1.0, CFG, risk_off=False)        # ATR 1% of price
    volatile = buy_levels(100.0, 20.0, 1.0, CFG, risk_off=False)   # ATR 20% of price
    assert volatile.suggested_weight < calm.suggested_weight


def test_volatility_damping_only_applies_above_the_five_percent_threshold():
    just_under = buy_levels(100.0, 4.9, 1.0, CFG, risk_off=False)
    just_over = buy_levels(100.0, 10.0, 1.0, CFG, risk_off=False)
    assert just_under.suggested_weight == pytest.approx(CFG.max_position_weight, abs=1e-4)
    assert just_over.suggested_weight < CFG.max_position_weight


def test_risk_off_halves_the_position():
    on = buy_levels(100.0, 1.0, 0.8, CFG, risk_off=False)
    off = buy_levels(100.0, 1.0, 0.8, CFG, risk_off=True)
    assert off.suggested_weight == pytest.approx(
        on.suggested_weight * CFG.risk_off_size_factor, abs=1e-4
    )


# ------------------------- trailing stop on a hold -------------------------

def _view(price: float, atr: float, recent_high: float) -> TechnicalView:
    return TechnicalView(price=price, atr=atr, rsi=50.0, sma50=None, sma200=None,
                         recent_high=recent_high)


def test_hold_stop_hangs_off_the_recent_high_not_todays_price():
    """This is the whole point of the trailing stop.

    A stop measured from the current price walks down with the position, so it
    can never actually be hit — it only ever reports a level a fixed distance
    below wherever the price already is. Anchoring to the recent high means a
    position that has given back its gains has a stop that stayed put.
    """
    view = _view(price=90.0, atr=2.0, recent_high=120.0)
    assert hold_stop(view, CFG) == pytest.approx(120.0 - CFG.atr_stop_mult * 2.0)


def test_hold_stop_ratchets_up_as_the_position_makes_new_highs():
    early = hold_stop(_view(100.0, 2.0, recent_high=100.0), CFG)
    later = hold_stop(_view(140.0, 2.0, recent_high=140.0), CFG)
    assert later > early


def test_hold_stop_uses_the_price_when_it_is_the_new_high():
    view = _view(price=150.0, atr=2.0, recent_high=140.0)
    assert hold_stop(view, CFG) == pytest.approx(150.0 - CFG.atr_stop_mult * 2.0)


def test_hold_stop_stays_positive_for_a_wildly_volatile_name():
    assert hold_stop(_view(5.0, 100.0, recent_high=5.0), CFG) > 0


def test_a_breach_is_detected_when_price_falls_to_the_stop():
    view = _view(price=80.0, atr=2.0, recent_high=120.0)
    stop = hold_stop(view, CFG)          # 116.0
    assert stop_breached(view, stop) is True


def test_no_breach_while_the_position_is_above_its_stop():
    view = _view(price=118.0, atr=2.0, recent_high=120.0)
    assert stop_breached(view, hold_stop(view, CFG)) is False
