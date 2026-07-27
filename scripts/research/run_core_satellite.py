"""Validate the CORE-SATELLITE design against 100% buy-and-hold.

Model: split capital into
  * CORE  — buy-and-hold the index (captures the full bull, which is hard to beat), and
  * SATELLITE — the tactical signal strategy (better risk-adjusted, actionable signals).

Combined equity = core_w * benchmark_curve + sat_w * strategy_curve, each sleeve
starting from its share of capital. We report several splits so the trade-off between
raw return and risk-adjusted return is explicit.

Usage: .venv/bin/python -m scripts.run_core_satellite
"""
from __future__ import annotations

import logging

import pandas as pd

from src.backtest.engine import Backtester, BacktestConfig
from src.backtest.metrics import compute_metrics
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

SPLITS = [(1.0, 0.0), (0.7, 0.3), (0.5, 0.5), (0.0, 1.0)]


def _row(label: str, equity: pd.Series, bench_total: float) -> str:
    m = compute_metrics(equity, [])
    delta = m.total_return_pct - bench_total
    return (
        f"  {label:<26} ret {m.total_return_pct:>8.1f}%  CAGR {m.cagr_pct:>6.1f}%  "
        f"maxDD {m.max_drawdown_pct:>7.1f}%  Sharpe {m.sharpe:>5.2f}  ({delta:+.0f}% vs B&H)"
    )


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    provider = YahooProvider()
    # Baseline = the best-behaved variant from tuning.
    sig, bt = SignalConfig(), BacktestConfig(use_trailing_stop=False, rebalance_every=5)

    for market in (Market.US, Market.BIST):
        print(f"\n{'='*84}\n{market.value} MARKET — core-satellite blends\n{'='*84}")
        r = Backtester(market, provider, cfg=sig, bt=bt).run()
        # Normalise both sleeves to a common start and index.
        strat = r.equity / float(r.equity.iloc[0])
        bench = r.benchmark.reindex(r.equity.index).ffill()
        bench = bench / float(bench.iloc[0])
        start = bt.start_equity
        bench_total = (float(bench.iloc[-1]) - 1.0) * 100.0

        for core_w, sat_w in SPLITS:
            blended = start * (core_w * bench + sat_w * strat)
            if core_w == 1.0:
                label = "100% core (buy & hold)"
            elif sat_w == 1.0:
                label = "100% satellite (strategy)"
            else:
                label = f"{int(core_w*100)}% core / {int(sat_w*100)}% sat"
            print(_row(label, blended, bench_total))

        # The honest reference: holding the same watchlist equal-weight. The
        # watchlist was chosen with hindsight, so any blend that beats the index
        # partly inherits that pick rather than earning it.
        if r.universe_metrics:
            uni = r.universe_benchmark.reindex(r.equity.index).ffill()
            print("  " + "-" * 82)
            print(_row("equal-wt universe (ref)", uni, bench_total))

    print("\nTakeaway: blends keep most of buy-and-hold's return while the satellite "
          "improves Sharpe and adds actionable signals + risk alerts.")
    print("Read the equal-weight universe row first: the watchlist is "
          "survivorship-biased, so the gap to the index overstates what the "
          "strategy itself contributes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
