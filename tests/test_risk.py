"""Position sizing and level placement — the guardrails that bound a bad trade.

SPEC section 1 requires a mandatory stop on every entry and a hard cap on any single
position. Both are asserted here, along with the volatility damping that stops a
wide-ATR name from turning a 2-ATR stop into an oversized monetary loss.
"""
from __future__ import annotations

import pytest

from src.signals.config import SignalConfig
from src.signals.risk import buy_levels, hold_stop

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


def test_hold_stop_sits_below_the_current_price():
    stop = hold_stop(100.0, 2.0, CFG)
    assert stop == pytest.approx(100.0 - CFG.atr_stop_mult * 2.0)


def test_hold_stop_stays_positive_for_a_wildly_volatile_name():
    assert hold_stop(5.0, 100.0, CFG) > 0
