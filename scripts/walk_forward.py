"""Walk-forward (out-of-sample) validation of the core-satellite strategy.

Instead of one big backtest over all history (which risks overfitting), this runs the
SAME fixed strategy separately on each calendar year and compares it to buy-and-hold
for that same year. Consistency across independent years is the real evidence the edge
isn't a fluke of one lucky period.

Reported per year: the 70/30 core-satellite blend vs 100% buy-and-hold.

Usage: .venv/bin/python -m scripts.walk_forward
"""
from __future__ import annotations

import logging

from src.backtest.engine import Backtester, BacktestConfig
from src.backtest.metrics import compute_metrics
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

CORE_W, SAT_W = 0.7, 0.3

# (label, trade_start, trade_end). 2022 starts after the ~200-bar indicator warmup.
FOLDS = [
    ("2022", "2022-04-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
    ("2026 YTD", "2026-01-01", "2026-12-31"),
]


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    provider = YahooProvider()
    sig = SignalConfig()

    for market in (Market.US, Market.BIST):
        print(f"\n{'='*88}\n{market.value} — walk-forward (70/30 core-satellite vs buy & hold, per year)\n{'='*88}")
        print(f"  {'Year':<9} {'Blend ret':>10} {'B&H ret':>9} {'Δret':>7} "
              f"{'Blend Sh':>9} {'B&H Sh':>7} {'Blend DD':>9} {'B&H DD':>7}  verdict")
        print("  " + "-" * 84)

        beat_sharpe = kept_return = better_dd = total = 0
        for label, start, end in FOLDS:
            bt = BacktestConfig(use_trailing_stop=False, rebalance_every=5,
                                trade_start=start, trade_end=end)
            try:
                r = Backtester(market, provider, cfg=sig, bt=bt).run()
            except Exception as exc:  # noqa: BLE001
                print(f"  {label:<9} skipped: {exc}")
                continue
            blend_equity = CORE_W * r.benchmark + SAT_W * r.equity
            bm = compute_metrics(blend_equity, [])
            bh = r.benchmark_metrics

            d_ret = bm.total_return_pct - bh.total_return_pct
            sharpe_ok = bm.sharpe >= bh.sharpe
            ret_ok = d_ret >= -3.0           # kept ~all of buy-and-hold's return
            dd_ok = bm.max_drawdown_pct >= bh.max_drawdown_pct  # shallower (less negative)
            total += 1
            beat_sharpe += sharpe_ok
            kept_return += ret_ok
            better_dd += dd_ok
            verdict = "".join(["S" if sharpe_ok else "·", "R" if ret_ok else "·",
                               "D" if dd_ok else "·"])
            print(f"  {label:<9} {bm.total_return_pct:>9.1f}% {bh.total_return_pct:>8.1f}% "
                  f"{d_ret:>+6.1f}% {bm.sharpe:>9.2f} {bh.sharpe:>7.2f} "
                  f"{bm.max_drawdown_pct:>8.1f}% {bh.max_drawdown_pct:>6.1f}%  {verdict}")

        if total:
            print("  " + "-" * 84)
            print(f"  Consistency: better/equal Sharpe {beat_sharpe}/{total} | "
                  f"kept return (≥ -3%) {kept_return}/{total} | "
                  f"shallower drawdown {better_dd}/{total}")
    print("\nKey: verdict flags S=better-Sharpe, R=kept-return, D=shallower-drawdown per year.")
    print("Each year is independent and out-of-sample for the fixed strategy — look for "
          "consistency, not one big year.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
