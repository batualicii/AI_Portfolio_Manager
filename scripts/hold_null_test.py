"""Is the ranking skill, or is it the universe? Measure it against random picks.

The selection run produced a return that should not come out of a simple momentum
rule. Before believing it, this asks the only question that separates the two
explanations: run the *same* simulation many times picking names **at random**
from the same universe on the same dates, and see where the ranking lands in that
distribution.

  ranking near the middle of the random cloud -> the ranking adds nothing; the
      universe and the concentration produced the number
  ranking far out in the right tail          -> there is real selection skill

This matters more than usual here because the universe is current S&P 500
membership. A stock that surged and then collapsed out of the index is absent, so
momentum's characteristic failure — buying a top and riding it down — has been
deleted from the data. That bias does **not** cancel against the equal-weight
benchmark: a static holder is barely affected, while a momentum selector
concentrates precisely into the names whose survival was guaranteed. The random
control is subject to the same distortion, which is what makes it the fair test.

Usage:
    python -m scripts.hold_null_test US               # 200 random portfolios
    python -m scripts.hold_null_test US --trials 500
    python -m scripts.hold_null_test US --top 8 --sweep
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import random
import statistics

from src.backtest.hold_engine import HoldBacktester, HoldConfig
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
SP500 = ROOT / "universes" / "sp500.json"


def _universe(market: Market) -> tuple[tuple[str, ...], str]:
    if market is Market.US and SP500.exists():
        data = json.loads(SP500.read_text())
        return tuple(data["symbols"]), f"{data['name']} ({data['fetched_at']})"
    cfg = SignalConfig()
    return cfg.universe(market), "built-in watchlist"


def _random_selector(rng: random.Random):
    def pick(ranked, top_n):
        names = [sym for _, sym in ranked]
        return rng.sample(names, min(top_n, len(names)))
    return pick


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market", nargs="?", default="US", choices=["US", "BIST"])
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--sweep", action="store_true",
                        help="also report how the ranking behaves at other basket sizes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    market = Market(args.market)
    symbols, source = _universe(market)
    cfg = dataclasses.replace(SignalConfig(), **(
        {"us_universe": symbols} if market is Market.US else {"bist_universe": symbols}
    ))
    hold = HoldConfig(top_n=args.top)
    provider = YahooProvider()

    print(f"\n{'=' * 78}\n{market.value} — is the ranking skill, or is it the universe?"
          f"\nuniverse: {source} ({len(symbols)} names), holding {args.top}"
          f"\n{'=' * 78}")

    ranked_run = HoldBacktester(market, provider, cfg=cfg, hold=hold).run()
    momentum_ret = ranked_run.metrics.total_return_pct
    print(f"\n  momentum ranking : {momentum_ret:>9.1f}%   "
          f"Sharpe {ranked_run.metrics.sharpe:.2f}  maxDD {ranked_run.metrics.max_drawdown_pct:.1f}%")
    print(f"  equal-weight all : {ranked_run.universe_metrics.total_return_pct:>9.1f}%   "
          f"Sharpe {ranked_run.universe_metrics.sharpe:.2f}  "
          f"maxDD {ranked_run.universe_metrics.max_drawdown_pct:.1f}%")

    print(f"\n  running {args.trials} random {args.top}-name portfolios "
          f"(same dates, same universe)...", flush=True)
    rng = random.Random(args.seed)
    returns, sharpes, drawdowns = [], [], []
    for i in range(args.trials):
        r = HoldBacktester(market, provider, cfg=cfg, hold=hold,
                           selector=_random_selector(rng)).run()
        returns.append(r.metrics.total_return_pct)
        sharpes.append(r.metrics.sharpe)
        drawdowns.append(r.metrics.max_drawdown_pct)
        if (i + 1) % 25 == 0:
            print(f"    {i + 1}/{args.trials}", flush=True)

    returns.sort()
    beaten = sum(1 for r in returns if r < momentum_ret)
    pct = beaten / len(returns) * 100.0

    def q(p: float) -> float:
        return returns[min(int(len(returns) * p), len(returns) - 1)]

    print(f"\n  random portfolios: median {statistics.median(returns):>8.1f}%   "
          f"mean {statistics.mean(returns):>8.1f}%")
    print(f"    5th pct {q(0.05):>8.1f}%   25th {q(0.25):>8.1f}%   "
          f"75th {q(0.75):>8.1f}%   95th {q(0.95):>8.1f}%   max {returns[-1]:>8.1f}%")
    print(f"    median Sharpe {statistics.median(sharpes):.2f}   "
          f"median maxDD {statistics.median(drawdowns):.1f}%")

    print(f"\n  the momentum ranking beat {beaten}/{len(returns)} random portfolios "
          f"({pct:.0f}th percentile)")

    if pct >= 95:
        verdict = ("REAL SELECTION SKILL — the ranking sits in the top 5% of what "
                   "random picking from this universe produces.")
    elif pct >= 75:
        verdict = ("WEAK EVIDENCE — better than most random picks, but well inside "
                   "the range luck produces. Not enough to trade on.")
    else:
        verdict = ("NO SKILL — a random pick from this universe does about as well. "
                   "The return came from the universe and the concentration, not "
                   "from the ranking.")
    print(f"\n  VERDICT: {verdict}")

    if args.sweep:
        print(f"\n  basket-size sensitivity (skill should degrade gently; a cliff means "
              f"\n  the result rests on a few names):")
        for n in (4, 8, 20, 50):
            if n > len(symbols):
                continue
            r = HoldBacktester(market, provider, cfg=cfg,
                               hold=dataclasses.replace(hold, top_n=n)).run()
            print(f"    top {n:>3}: {r.metrics.total_return_pct:>9.1f}%   "
                  f"Sharpe {r.metrics.sharpe:>5.2f}   maxDD {r.metrics.max_drawdown_pct:>7.1f}%")

    print("\n  Reminder: the universe is CURRENT index membership, so names that "
          "\n  collapsed out of the index are absent. That deletes momentum's worst "
          "\n  outcomes from the data and flatters this strategy specifically — the "
          "\n  random control shares the distortion, which is why it is the fair test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
