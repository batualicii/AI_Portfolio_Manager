"""Search indicator/parameter weight combinations — WITHOUT overfitting.

Methodology (this is the part that matters):
  1. OPTIMIZE on a training window (2022-04 .. 2024-12) only.
  2. Rank combinations by the 70/30 core-satellite blend's Sharpe, averaged across
     US + BIST (risk-adjusted return is the honest objective — see SPEC §0).
  3. VALIDATE the top combinations on a held-out test window (2025 .. 2026 YTD) they
     never saw. A combo that is great in training but poor in test is OVERFIT — we
     reject it and prefer combos that are robust in BOTH.

Honest limitation: fundamentals & sentiment have no free historical data, so they are
neutral in every backtest. This searches the BACKTESTABLE backbone: technical
sub-weights (trend/momentum/mean-reversion), macro-regime weight, entry threshold,
and rebalance frequency.

Usage: .venv/bin/python -m scripts.optimize_weights
"""
from __future__ import annotations

import dataclasses
import itertools
import logging

from src.backtest.engine import Backtester, BacktestConfig
from src.backtest.metrics import compute_metrics
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

TRAIN = ("2022-04-01", "2024-12-31")
TEST = ("2025-01-01", "2026-12-31")
CORE_W, SAT_W = 0.7, 0.3

# --- search space (kept focused to stay tractable and avoid over-searching) ---
MACRO_SHARE = [0.10, 0.20, 0.35]          # macro-regime weight vs technical
TECH_PROFILES = {                          # trend / momentum / mean-reversion
    "balanced":   (0.45, 0.30, 0.25),
    "trend":      (0.60, 0.25, 0.15),
    "momentum":   (0.30, 0.45, 0.25),
    "meanrev":    (0.30, 0.25, 0.45),
}
BUY_THRESHOLDS = [0.30, 0.40]
REBALANCES = [5, 10]


def make_config(macro_share: float, profile: str, buy_thr: float) -> SignalConfig:
    tr, mo, mr = TECH_PROFILES[profile]
    return dataclasses.replace(
        SignalConfig(),
        w_technical=1.0 - macro_share, w_macro=macro_share,
        w_trend=tr, w_momentum=mo, w_meanrev=mr,
        buy_threshold=buy_thr,
    )


def blend_sharpe(provider, sig, rebalance, window) -> dict:
    """Return {market: (blend_metrics, benchmark_metrics)} for a window."""
    out = {}
    for mkt in (Market.US, Market.BIST):
        bt = BacktestConfig(use_trailing_stop=False, rebalance_every=rebalance,
                            trade_start=window[0], trade_end=window[1])
        r = Backtester(mkt, provider, cfg=sig, bt=bt).run()
        blend = CORE_W * r.benchmark + SAT_W * r.equity
        out[mkt] = (compute_metrics(blend, []), r.benchmark_metrics)
    return out


def objective(res: dict) -> float:
    """Average blend Sharpe across markets — the score we optimize."""
    return sum(m.sharpe for m, _ in res.values()) / len(res)


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    provider = YahooProvider()

    combos = list(itertools.product(MACRO_SHARE, TECH_PROFILES, BUY_THRESHOLDS, REBALANCES))
    print(f"Searching {len(combos)} combinations on TRAIN {TRAIN[0]}..{TRAIN[1]} "
          f"(objective: mean 70/30 blend Sharpe across US+BIST)\n")

    scored = []
    for macro_share, profile, buy_thr, reb in combos:
        sig = make_config(macro_share, profile, buy_thr)
        try:
            res = blend_sharpe(provider, sig, reb, TRAIN)
        except Exception as exc:  # noqa: BLE001
            print(f"  combo failed: {exc}")
            continue
        scored.append(((macro_share, profile, buy_thr, reb), objective(res)))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Baseline = current shipped defaults, for reference.
    base = SignalConfig()
    base_train = objective(blend_sharpe(provider, base, 5, TRAIN))
    base_test = objective(blend_sharpe(provider, base, 5, TEST))

    print(f"{'rank':<5}{'macro':>6}{'tech-profile':>14}{'buyThr':>8}{'reb':>5}"
          f"{'TRAIN Sh':>10}{'TEST Sh':>9}{'Δ(test-train)':>14}")
    print("-" * 71)
    print(f"{'base':<5}{0.15:>6.2f}{'balanced':>14}{0.35:>8.2f}{5:>5}"
          f"{base_train:>10.2f}{base_test:>9.2f}{base_test-base_train:>+14.2f}")
    print("-" * 71)

    for rank, ((macro_share, profile, buy_thr, reb), train_sh) in enumerate(scored[:8], 1):
        sig = make_config(macro_share, profile, buy_thr)
        test_sh = objective(blend_sharpe(provider, sig, reb, TEST))
        print(f"{rank:<5}{macro_share:>6.2f}{profile:>14}{buy_thr:>8.2f}{reb:>5}"
              f"{train_sh:>10.2f}{test_sh:>9.2f}{test_sh-train_sh:>+14.2f}")

    print("\nRobust = high on BOTH train and test with small Δ. A combo that's top on "
          "train but collapses on test is overfit — ignore it.")
    print("Note: fundamentals & sentiment weights are neutral here (no historical data) "
          "and are NOT optimized — keep their live defaults.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
