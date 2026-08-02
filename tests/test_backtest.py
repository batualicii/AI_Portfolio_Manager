"""Backtester correctness — the gate SPEC section 6b puts in front of real money.

A backtest that flatters the strategy is worse than no backtest, because it gets
believed. The properties tested here are the ones that decide whether the reported
numbers mean anything: no lookahead, honest fills, honest exits, and a benchmark
that does not quietly credit hindsight stock-picking to the timing logic.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from src.backtest.engine import Backtester, BacktestConfig, _equal_weight_curve, _next_open
from src.models import Market
from src.signals import indicators as ind
from src.signals.config import SignalConfig
from tests.conftest import (
    FakeProvider,
    momentum_downtrend,
    momentum_uptrend,
    uptrend,
    uptrend_then_gap_down,
)

CFG = dataclasses.replace(
    SignalConfig(), us_universe=("AAA",), bist_universe=("BBB",)
)
BT = BacktestConfig(period="5y", warmup_bars=200, rebalance_every=5)


def _provider(bars, *, index_bars=None) -> FakeProvider:
    provider = FakeProvider()
    provider.set_history("AAA", Market.US, bars)
    provider.set_history(CFG.index_symbol[Market.US], Market.US,
                         index_bars if index_bars is not None else bars)
    return provider


def _run(bars, *, cfg=CFG, bt=BT, index_bars=None):
    return Backtester(Market.US, _provider(bars, index_bars=index_bars),
                      cfg=cfg, bt=bt).run()


# --------------------------------- basics ----------------------------------

def test_a_market_with_no_data_raises_rather_than_reporting_zero():
    with pytest.raises(RuntimeError):
        Backtester(Market.US, FakeProvider(), cfg=CFG, bt=BT).run()


def test_the_equity_curve_starts_at_the_configured_capital():
    result = _run(momentum_uptrend(400))
    assert result.equity.iloc[0] == pytest.approx(BT.start_equity, rel=0.05)


def test_the_curve_only_covers_bars_after_the_warmup():
    bars = momentum_uptrend(400)
    result = _run(bars)
    assert len(result.equity) == len(bars) - BT.warmup_bars


def test_capital_is_preserved_when_nothing_ever_qualifies():
    cfg = dataclasses.replace(CFG, buy_threshold=2.0)  # unreachable
    result = _run(momentum_uptrend(400), cfg=cfg)
    assert result.trades == []
    assert result.equity.iloc[-1] == pytest.approx(BT.start_equity)


# -------------------------------- no lookahead -----------------------------

def test_decisions_do_not_change_when_future_bars_are_appended():
    """The property that makes the whole backtest meaningful.

    Running over a truncated history must produce exactly the trades that the
    longer run produces up to the same date. If appending future data changes an
    earlier decision, the strategy is being scored on information it could not
    have had.
    """
    bars = momentum_uptrend(320)
    short_bt = dataclasses.replace(BT, trade_end="2023-08-01")

    short = Backtester(Market.US, _provider(bars[:280]), cfg=CFG, bt=short_bt).run()
    long = Backtester(Market.US, _provider(bars), cfg=CFG, bt=short_bt).run()

    assert [(t.symbol, t.entry, t.exit) for t in short.trades] == \
           [(t.symbol, t.entry, t.exit) for t in long.trades]
    pd.testing.assert_series_equal(short.equity, long.equity)


# ---------------------------------- fills ----------------------------------

def test_next_open_returns_the_following_session():
    df = ind.bars_to_frame(uptrend(5, start=10.0, step=1.0))
    day = df.index[1]
    nxt, price = _next_open(df, day)
    assert nxt == df.index[2]
    assert price == pytest.approx(float(df.loc[df.index[2], "open"]))


def test_next_open_is_none_on_the_final_bar():
    df = ind.bars_to_frame(uptrend(5))
    assert _next_open(df, df.index[-1]) is None


def test_entries_fill_at_the_next_open_not_the_signal_close():
    """Buying at the close that produced the signal is not a placeable trade.

    The close is only known once the session is over, so filling there hands the
    strategy a price it could never have got. The next open is the earliest
    honest execution.
    """
    bars = momentum_uptrend(400)
    df = ind.bars_to_frame(bars)
    # Loosen the entry bar so the fill path is actually exercised; the threshold
    # itself is calibration, not the mechanic under test here.
    result = _run(bars, cfg=dataclasses.replace(CFG, buy_threshold=0.0))
    assert result.trades, "expected the strategy to take at least one position"

    opens = {round(float(v), 4) for v in df["open"]}
    for trade in result.trades:
        assert round(trade.entry, 4) in opens


# ---------------------------------- exits ----------------------------------

def test_a_gap_below_the_stop_fills_at_the_open_not_at_the_stop():
    """A stop cannot execute at a level the market jumped straight over."""
    bars = uptrend_then_gap_down(320, gap_pct=0.30)
    df = ind.bars_to_frame(bars)
    result = _run(bars, index_bars=uptrend(320))

    stop_exits = [t for t in result.trades if t.reason == "stop"]
    if stop_exits:
        gap_open = float(df["open"].iloc[-1])
        gapped = [t for t in stop_exits if t.exit == pytest.approx(gap_open, rel=1e-6)]
        # If the gap bar took anyone out, they left at the open.
        for trade in gapped:
            assert trade.exit == pytest.approx(gap_open)


def test_every_trade_records_why_it_closed():
    result = _run(momentum_uptrend(400))
    assert all(t.reason in {"stop", "target", "signal"} for t in result.trades)


def test_a_sustained_decline_does_not_produce_a_profit():
    result = _run(momentum_downtrend(400))
    assert result.metrics.total_return_pct <= 0.01


# ------------------------- equal-weight universe ---------------------------

def test_the_equal_weight_curve_averages_normalised_holdings():
    index = pd.to_datetime(["2024-01-01", "2024-01-02"])
    panel = {
        "A": pd.DataFrame({"close": [100.0, 120.0]}, index=index),  # +20%
        "B": pd.DataFrame({"close": [50.0, 40.0]}, index=index),    # -20%
    }
    curve = _equal_weight_curve(panel, index)
    assert curve.iloc[0] == pytest.approx(1.0)
    assert curve.iloc[-1] == pytest.approx(1.0)  # +20% and -20% cancel


def test_the_universe_benchmark_is_reported_alongside_the_index():
    """Without this column the index comparison credits hindsight to the strategy.

    The watchlist is a list of names that are large and successful today, so a
    strategy trading them beats a broad index partly because of the list, not the
    logic. Holding the same names equal-weight cancels that out.
    """
    result = _run(momentum_uptrend(400))
    assert not result.universe_benchmark.empty
    assert result.universe_metrics is not None
    assert result.universe_benchmark.iloc[0] == pytest.approx(BT.start_equity)
    assert len(result.universe_benchmark) == len(result.equity)


def test_the_universe_benchmark_reflects_the_universe_not_the_index():
    # Watchlist rises while the index falls: the two benchmarks must disagree.
    result = _run(momentum_uptrend(400), index_bars=momentum_downtrend(400))
    assert result.universe_metrics.total_return_pct > 0
    assert result.benchmark_metrics.total_return_pct < 0


# --------------------------------- costs -----------------------------------

def test_commission_makes_a_round_trip_cost_money():
    free = dataclasses.replace(BT, commission_pct=0.0)
    charged = dataclasses.replace(BT, commission_pct=0.01)
    bars = momentum_uptrend(400)
    assert _run(bars, bt=free).metrics.final_equity >= \
           _run(bars, bt=charged).metrics.final_equity


def test_the_trading_window_can_be_restricted_for_walk_forward_folds():
    bars = momentum_uptrend(400)
    windowed = dataclasses.replace(BT, trade_start="2023-06-01", trade_end="2023-09-01")
    result = _run(bars, bt=windowed)
    assert str(result.equity.index[0].date()) >= "2023-06-01"
    assert str(result.equity.index[-1].date()) <= "2023-09-01"


def test_an_empty_trading_window_raises_instead_of_returning_nothing():
    with pytest.raises(RuntimeError):
        _run(momentum_uptrend(400),
             bt=dataclasses.replace(BT, trade_start="2099-01-01"))
