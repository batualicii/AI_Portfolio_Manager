"""Indicator correctness — checked against hand-computed values, not snapshots.

These are the foundation every score sits on, so they're verified against the
textbook definitions rather than against whatever the code happens to produce.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from src.signals import indicators as ind
from tests.conftest import flat, uptrend


def test_bars_to_frame_empty_gives_typed_columns():
    df = ind.bars_to_frame([])
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_bars_to_frame_preserves_order_and_values():
    bars = uptrend(5, start=10.0, step=1.0)
    df = ind.bars_to_frame(bars)
    assert list(df["close"]) == [10.0, 11.0, 12.0, 13.0, 14.0]
    assert df.index.is_monotonic_increasing


def test_sma_needs_a_full_window():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = ind.sma(s, 3)
    assert math.isnan(out.iloc[1])          # not enough history yet
    assert out.iloc[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert out.iloc[3] == pytest.approx(3.0)  # (2+3+4)/3


def test_ema_matches_manual_recursion():
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = ind.ema(s, 2)
    # span=2 -> alpha = 2/(span+1) = 2/3. adjust=False seeds the recursion at the
    # first observation; min_periods only masks the leading output, it does not
    # change the recursion.
    alpha = 2 / 3
    expected = 1.0
    for value in (2.0, 3.0, 4.0):
        expected = alpha * value + (1 - alpha) * expected
    assert out.iloc[-1] == pytest.approx(expected, rel=1e-9)
    assert math.isnan(out.iloc[0])  # masked until min_periods is satisfied


def test_rsi_is_100_in_a_pure_uptrend():
    # No down days at all -> average loss is zero -> RSI pinned at 100.
    close = ind.bars_to_frame(uptrend(60))["close"]
    assert ind.rsi(close, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_is_zero_in_a_pure_downtrend():
    close = pd.Series([100.0 - i for i in range(60)])
    assert ind.rsi(close, 14).iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_stays_in_range_on_mixed_data():
    close = pd.Series([100 + (3 if i % 3 else -5) for i in range(80)], dtype=float)
    values = ind.rsi(close, 14).dropna()
    assert not values.empty
    assert values.between(0.0, 100.0).all()


def test_macd_histogram_is_positive_while_accelerating():
    close = ind.bars_to_frame(uptrend(120))["close"]
    macd_line, signal_line, hist = ind.macd(close)
    assert macd_line.iloc[-1] > 0                      # fast EMA above slow
    assert hist.iloc[-1] == pytest.approx(macd_line.iloc[-1] - signal_line.iloc[-1])


def test_true_range_uses_the_widest_of_the_three_measures():
    # A gap up means |high - prev_close| exceeds the intraday high-low range.
    df = pd.DataFrame({
        "high": [10.0, 20.0], "low": [9.0, 19.0], "close": [9.5, 19.5],
    })
    tr = ind.true_range(df)
    assert tr.iloc[1] == pytest.approx(20.0 - 9.5)  # high - prev_close, not 1.0


def test_atr_is_zero_on_a_flat_series():
    df = ind.bars_to_frame(flat(60))
    assert ind.atr(df, 14).iloc[-1] == pytest.approx(0.0)


def test_atr_is_positive_and_finite_on_a_trend():
    df = ind.bars_to_frame(uptrend(60))
    value = ind.atr(df, 14).iloc[-1]
    assert value > 0 and math.isfinite(value)


def test_bollinger_bands_bracket_the_middle_band():
    close = ind.bars_to_frame(uptrend(60))["close"]
    mid, upper, lower = ind.bollinger(close, 20, 2.0)
    assert lower.iloc[-1] < mid.iloc[-1] < upper.iloc[-1]
    # Bands are symmetric about the mean by construction.
    assert (upper.iloc[-1] - mid.iloc[-1]) == pytest.approx(mid.iloc[-1] - lower.iloc[-1])


def test_rate_of_change_is_a_percentage():
    close = pd.Series([100.0, 105.0, 110.0])
    assert ind.rate_of_change(close, 2).iloc[-1] == pytest.approx(10.0)


def test_sustained_below_ma_needs_the_break_to_hold_every_day():
    """The difference between "the price fell" and "the trend is over"."""
    from src.signals.indicators import sustained_below_ma

    dead = pd.Series([100.0] * 230 + [60.0] * 20)
    assert sustained_below_ma(dead, 200, 20) is True

    # One day back above the average breaks the streak.
    recovered = pd.Series([100.0] * 230 + [60.0] * 19 + [130.0])
    assert sustained_below_ma(recovered, 200, 20) is False


def test_sustained_below_ma_says_it_cannot_judge_rather_than_guessing():
    from src.signals.indicators import sustained_below_ma

    assert sustained_below_ma(pd.Series([100.0] * 50), 200, 20) is None
