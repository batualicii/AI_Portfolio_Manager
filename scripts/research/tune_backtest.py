"""Compare a few PRINCIPLED strategy variants against buy-and-hold.

Not a parameter grid-search (that overfits). Each variant embodies one hypothesis
about why the baseline trailed, so we can see which structural idea actually helps.

Usage: .venv/bin/python -m scripts.tune_backtest
"""
from __future__ import annotations

import dataclasses
import logging

from src.backtest.engine import Backtester, BacktestConfig
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

# --- variants -------------------------------------------------------------

BASE_SIG = SignalConfig()

VARIANTS: dict[str, tuple[SignalConfig, BacktestConfig]] = {
    "baseline (fixed target, weekly)": (
        BASE_SIG,
        BacktestConfig(use_trailing_stop=False, rebalance_every=5),
    ),
    "trail winners (3 ATR, biweekly)": (
        dataclasses.replace(BASE_SIG, sell_threshold=-0.45,
                            max_position_weight=0.20, base_position_weight=0.10),
        BacktestConfig(use_trailing_stop=True, trail_atr_mult=3.0, rebalance_every=10),
    ),
    "trail + concentrate (4 ATR)": (
        dataclasses.replace(BASE_SIG, sell_threshold=-0.50, buy_threshold=0.30,
                            max_position_weight=0.25, base_position_weight=0.12),
        BacktestConfig(use_trailing_stop=True, trail_atr_mult=4.0, rebalance_every=10),
    ),
}


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    provider = YahooProvider()

    for market in (Market.US, Market.BIST):
        print(f"\n{'='*82}\n{market.value} MARKET\n{'='*82}")
        bench_printed = False
        for name, (sig, bt) in VARIANTS.items():
            try:
                r = Backtester(market, provider, cfg=sig, bt=bt).run()
            except Exception as exc:  # noqa: BLE001
                print(f"  {name:<34} FAILED: {exc}")
                continue
            if not bench_printed:
                b = r.benchmark_metrics
                print(f"  {'BUY & HOLD (index)':<34} ret {b.total_return_pct:>8.1f}%  "
                      f"CAGR {b.cagr_pct:>6.1f}%  maxDD {b.max_drawdown_pct:>7.1f}%  "
                      f"Sharpe {b.sharpe:>5.2f}")
                print("  " + "-" * 78)
                bench_printed = True
            m = r.metrics
            beat = m.total_return_pct - r.benchmark_metrics.total_return_pct
            flag = "✓BEAT" if beat > 0 else "✗trail"
            print(f"  {name:<34} ret {m.total_return_pct:>8.1f}%  "
                  f"CAGR {m.cagr_pct:>6.1f}%  maxDD {m.max_drawdown_pct:>7.1f}%  "
                  f"Sharpe {m.sharpe:>5.2f}  {flag} ({beat:+.0f}%)  [{m.num_trades} trades]")
    print("\nReminder: tuned on in-sample history — promising variants still need "
          "walk-forward (out-of-sample) checks before real money.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
