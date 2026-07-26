"""Selection/hold backtester — the mechanics that decide whether its verdict counts.

The point of this engine is to answer "does ranking beat holding everything?", so
the ranking has to be right, the holding has to actually hold, and the benchmark
has to be the same universe. Each of those is pinned here.
"""
from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from src.backtest.hold_engine import HoldBacktester, HoldConfig, momentum_12_1
from src.models import Market
from src.signals import indicators as ind
from src.signals.config import SignalConfig
from tests.conftest import FakeProvider, _bars, flat, momentum_downtrend, momentum_uptrend

HOLD = HoldConfig(momentum_lookback=60, momentum_skip=5, rebalance_days=20, top_n=2)


def _cfg(*symbols: str) -> SignalConfig:
    return dataclasses.replace(SignalConfig(), us_universe=symbols, bist_universe=())


def _provider(bars_by_symbol: dict, index_bars) -> FakeProvider:
    p = FakeProvider()
    for sym, bars in bars_by_symbol.items():
        p.set_history(sym, Market.US, bars)
    p.set_history(SignalConfig().index_symbol[Market.US], Market.US, index_bars)
    return p


# ------------------------------- momentum ---------------------------------

def test_momentum_measures_the_window_ending_before_the_skip():
    # 100 bars rising by exactly 1 per bar.
    close = pd.Series([100.0 + i for i in range(100)])
    cfg = HoldConfig(momentum_lookback=50, momentum_skip=10)
    # start = 100 bars back from the end of the 60-bar window; end = 10 back.
    start = float(close.iloc[-60])
    end = float(close.iloc[-11])
    assert momentum_12_1(close, cfg) == pytest.approx(end / start - 1.0)


def test_momentum_skips_the_most_recent_bars():
    """A late crash inside the skip window must not affect the score.

    The skip exists because the most recent month tends to mean-revert; if a
    last-minute move changed the ranking, the signal would be a short-horizon
    one wearing a long-horizon label.
    """
    rising = [100.0 + i for i in range(100)]
    crashed = rising[:-5] + [10.0] * 5
    cfg = HoldConfig(momentum_lookback=50, momentum_skip=10)
    assert momentum_12_1(pd.Series(rising), cfg) == pytest.approx(
        momentum_12_1(pd.Series(crashed), cfg)
    )


def test_momentum_is_none_without_enough_history():
    # Absent must stay distinguishable from weak.
    assert momentum_12_1(pd.Series([1.0] * 10), HoldConfig()) is None


def test_momentum_is_positive_for_a_riser_and_negative_for_a_faller():
    up = ind.bars_to_frame(momentum_uptrend(400))["close"]
    down = ind.bars_to_frame(momentum_downtrend(400))["close"]
    assert momentum_12_1(up, HoldConfig()) > 0
    assert momentum_12_1(down, HoldConfig()) < 0


# -------------------------------- selection --------------------------------

def test_the_strongest_names_are_the_ones_held():
    strong_a = momentum_uptrend(300, daily_pct=0.010)
    strong_b = momentum_uptrend(300, daily_pct=0.008)
    weak = momentum_downtrend(300)
    provider = _provider(
        {"FAST": strong_a, "OK": strong_b, "BAD": weak}, momentum_uptrend(300)
    )
    result = HoldBacktester(
        Market.US, provider, cfg=_cfg("FAST", "OK", "BAD"), hold=HOLD
    ).run()

    assert result.holdings_log
    for _, held in result.holdings_log:
        assert "BAD" not in held          # the decliner is never selected
        assert set(held) <= {"FAST", "OK"}
        assert len(held) <= HOLD.top_n


def test_only_top_n_names_are_held_at_once():
    bars = {f"S{i}": momentum_uptrend(300, daily_pct=0.002 * (i + 1)) for i in range(5)}
    provider = _provider(bars, momentum_uptrend(300))
    result = HoldBacktester(Market.US, provider, cfg=_cfg(*bars), hold=HOLD).run()
    assert all(len(held) == HOLD.top_n for _, held in result.holdings_log)


def test_a_symbol_with_too_little_history_is_excluded():
    bars = {"GOOD": momentum_uptrend(300), "SHORT": momentum_uptrend(40)}
    provider = _provider(bars, momentum_uptrend(300))
    result = HoldBacktester(Market.US, provider, cfg=_cfg("GOOD", "SHORT"), hold=HOLD).run()
    for _, held in result.holdings_log:
        assert "SHORT" not in held


# ------------------------------- the holding -------------------------------

def test_positions_are_not_stopped_out_on_a_drawdown():
    """The defining property: a hold strategy rides the dip.

    A mid-window crash that would trigger any stop must leave the position in
    place until the next rebalance — being shaken out is what destroys the
    return this design exists to capture.
    """
    closes = [100.0 + i for i in range(150)] + [120.0] * 5 + [100.0 + 150 + i for i in range(145)]
    bars = _bars(closes)
    provider = _provider({"DIPPER": bars}, momentum_uptrend(300))
    cfg = _cfg("DIPPER")
    result = HoldBacktester(Market.US, provider, cfg=cfg, hold=HOLD).run()
    # It stays selected across the dip rather than being exited mid-quarter.
    assert result.holdings_log
    assert all("DIPPER" in held for _, held in result.holdings_log)


def test_rebalancing_happens_on_the_configured_cadence():
    bars = {"A": momentum_uptrend(400), "B": momentum_uptrend(400, daily_pct=0.004)}
    provider = _provider(bars, momentum_uptrend(400))
    hold = dataclasses.replace(HOLD, rebalance_days=20)
    result = HoldBacktester(Market.US, provider, cfg=_cfg("A", "B"), hold=hold).run()
    days = [d for d, _ in result.holdings_log]
    gaps = {(b - a).days for a, b in zip(days, days[1:])}
    assert gaps, "expected more than one rebalance"
    assert all(g >= 15 for g in gaps)  # calendar days for 20 synthetic bars


# ------------------------------- benchmarks --------------------------------

def test_all_three_curves_start_from_the_same_capital():
    bars = {"A": momentum_uptrend(400), "B": momentum_uptrend(400, daily_pct=0.004)}
    provider = _provider(bars, momentum_uptrend(400))
    result = HoldBacktester(Market.US, provider, cfg=_cfg("A", "B"), hold=HOLD).run()
    for curve in (result.equity, result.benchmark, result.universe_benchmark):
        assert curve.iloc[0] == pytest.approx(HOLD.start_equity, rel=0.02)
        assert len(curve) == len(result.equity)


def test_the_universe_benchmark_holds_every_name_not_just_the_picked_ones():
    """The comparison only means something if both sides share the same pool.

    Universe and strategy are drawn from the same survivorship-biased list, so
    the bias largely cancels and the gap is selection skill.
    """
    winner = momentum_uptrend(400, daily_pct=0.010)
    loser = momentum_downtrend(400)
    provider = _provider({"WIN": winner, "LOSE": loser}, flat(400))
    hold = dataclasses.replace(HOLD, top_n=1)
    result = HoldBacktester(Market.US, provider, cfg=_cfg("WIN", "LOSE"), hold=hold).run()

    # Picking only the winner must beat holding winner+loser equally.
    assert result.metrics.total_return_pct > result.universe_metrics.total_return_pct


def test_turnover_is_reported_so_trading_cost_is_visible():
    bars = {"A": momentum_uptrend(400), "B": momentum_downtrend(400)}
    provider = _provider(bars, momentum_uptrend(400))
    result = HoldBacktester(Market.US, provider, cfg=_cfg("A", "B"), hold=HOLD).run()
    assert result.turnover_pct > 0


# --------------------------------- guards ----------------------------------

def test_missing_data_raises_rather_than_reporting_an_empty_result():
    with pytest.raises(RuntimeError):
        HoldBacktester(Market.US, FakeProvider(), cfg=_cfg("NOPE"), hold=HOLD).run()


def test_the_window_can_be_restricted_for_walk_forward_folds():
    bars = {"A": momentum_uptrend(500), "B": momentum_uptrend(500, daily_pct=0.004)}
    provider = _provider(bars, momentum_uptrend(500))
    hold = dataclasses.replace(HOLD, trade_start="2023-09-01", trade_end="2023-12-01")
    result = HoldBacktester(Market.US, provider, cfg=_cfg("A", "B"), hold=hold).run()
    assert str(result.equity.index[0].date()) >= "2023-09-01"
    assert str(result.equity.index[-1].date()) <= "2023-12-01"


# ------------------------- swappable selector ------------------------------

def test_a_custom_selector_overrides_the_momentum_ranking():
    """The null test depends on this: same simulation, different picking rule."""
    bars = {"A": momentum_uptrend(400), "B": momentum_downtrend(400)}
    provider = _provider(bars, momentum_uptrend(400))

    always_b = lambda ranked, top_n: ["B"]  # noqa: E731
    result = HoldBacktester(
        Market.US, provider, cfg=_cfg("A", "B"), hold=dataclasses.replace(HOLD, top_n=1),
        selector=always_b,
    ).run()

    assert result.holdings_log
    assert all(held == ["B"] for _, held in result.holdings_log)


def test_the_default_selector_still_takes_the_highest_scores():
    from src.backtest.hold_engine import top_by_score
    ranked = [(0.1, "LOW"), (0.9, "HIGH"), (0.5, "MID")]
    assert top_by_score(ranked, 2) == ["HIGH", "MID"]


def test_a_selector_may_return_fewer_names_than_requested():
    bars = {"A": momentum_uptrend(400), "B": momentum_uptrend(400, daily_pct=0.004)}
    provider = _provider(bars, momentum_uptrend(400))
    one_only = lambda ranked, top_n: [ranked[0][1]]  # noqa: E731
    result = HoldBacktester(Market.US, provider, cfg=_cfg("A", "B"),
                            hold=HOLD, selector=one_only).run()
    assert all(len(held) == 1 for _, held in result.holdings_log)


# --------------------- point-in-time index membership ----------------------

def test_names_outside_the_index_on_that_date_are_not_eligible():
    """Without this the run assumes today's membership held in the past.

    That assumption is what deletes momentum's worst outcomes from the data —
    the names it bought near a top and rode down until they left the index.
    """
    bars = {"MEMBER": momentum_uptrend(400, daily_pct=0.004),
            "DROPPED": momentum_uptrend(400, daily_pct=0.010)}
    provider = _provider(bars, momentum_uptrend(400))

    # DROPPED has the stronger momentum, so it would be picked if eligible.
    result = HoldBacktester(
        Market.US, provider, cfg=_cfg("MEMBER", "DROPPED"),
        hold=dataclasses.replace(HOLD, top_n=1),
        members_at=lambda day: {"MEMBER"},
    ).run()

    assert result.holdings_log
    assert all(held == ["MEMBER"] for _, held in result.holdings_log)


def test_membership_can_change_over_time():
    bars = {"EARLY": momentum_uptrend(400, daily_pct=0.004),
            "LATE": momentum_uptrend(400, daily_pct=0.010)}
    provider = _provider(bars, momentum_uptrend(400))
    cutoff = pd.Timestamp("2023-06-01")

    result = HoldBacktester(
        Market.US, provider, cfg=_cfg("EARLY", "LATE"),
        hold=dataclasses.replace(HOLD, top_n=1),
        members_at=lambda day: {"EARLY"} if day < cutoff else {"LATE"},
    ).run()

    picks = {day: held[0] for day, held in result.holdings_log}
    assert any(sym == "EARLY" for day, sym in picks.items() if day < cutoff)
    assert any(sym == "LATE" for day, sym in picks.items() if day >= cutoff)


def test_without_membership_every_configured_name_stays_eligible():
    bars = {"A": momentum_uptrend(400), "B": momentum_uptrend(400, daily_pct=0.010)}
    provider = _provider(bars, momentum_uptrend(400))
    result = HoldBacktester(Market.US, provider, cfg=_cfg("A", "B"),
                            hold=dataclasses.replace(HOLD, top_n=1)).run()
    # B has stronger momentum and nothing filters it out.
    assert all(held == ["B"] for _, held in result.holdings_log)
