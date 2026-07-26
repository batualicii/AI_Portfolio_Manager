"""Test one hypothesis: does *selecting* names beat *holding all of them*?

Stage 8 found that holding the watchlist beat trading it, so the question worth
asking next is whether a ranking adds anything on top of holding everything. This
runs the hold engine (stopless, quarterly rebalance, 12-1 momentum, top N equally
weighted) against two references:

  * equal-weight hold of the SAME universe — the honest yardstick, because both
    sides draw from the same survivorship-biased pool so the bias largely cancels
    and what remains is selection skill;
  * the index — the "beat the market" question.

Reading the result:
  beats equal-weight  -> the ranking has selection skill; worth developing
  matches equal-weight -> the ranking is noise; holding everything is simpler
  trails equal-weight  -> the ranking actively destroys value

Usage:
    python -m scripts.run_hold_variant              # both markets, S&P 500 for US
    python -m scripts.run_hold_variant US           # one market
    python -m scripts.run_hold_variant US --top 12  # hold more names

Fetching 500 symbols of 5-year history takes a while and Yahoo rate-limits, so
expect this to run for several minutes on the first pass.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import sys

from src.backtest.hold_engine import HoldBacktester, HoldConfig
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
SP500 = ROOT / "universes" / "sp500.json"


def _load_us_universe() -> tuple[tuple[str, ...], str]:
    """Prefer the broad S&P 500 snapshot; fall back to the tactical watchlist."""
    if SP500.exists():
        data = json.loads(SP500.read_text())
        return tuple(data["symbols"]), f"{data['name']} ({data['fetched_at']})"
    return SignalConfig().us_universe, "built-in watchlist"


def _fmt(label: str, m) -> str:
    return (f"  {label:<28} ret {m.total_return_pct:>9.1f}%  CAGR {m.cagr_pct:>6.1f}%  "
            f"maxDD {m.max_drawdown_pct:>7.1f}%  Sharpe {m.sharpe:>5.2f}")


def run_one(market: Market, provider: YahooProvider, hold: HoldConfig) -> None:
    cfg = SignalConfig()
    if market is Market.US:
        symbols, source = _load_us_universe()
        cfg = dataclasses.replace(cfg, us_universe=symbols)
    else:
        symbols, source = cfg.bist_universe, "built-in watchlist"

    print(f"\n{'=' * 84}\n{market.value} — selection vs holding everything"
          f"\nuniverse: {source} ({len(symbols)} symbols), "
          f"holding top {hold.top_n}, rebalanced every {hold.rebalance_days} bars\n{'=' * 84}")

    if len(symbols) < hold.top_n * 3:
        print(f"  ⚠️  Only {len(symbols)} names to choose {hold.top_n} from — this is too "
              f"narrow to measure selection skill. Treat the result as uninformative.")

    result = HoldBacktester(market, provider, cfg=cfg, hold=hold).run()

    print(_fmt("Selection (top N, held)", result.metrics))
    print(_fmt("Equal-weight universe", result.universe_metrics))
    print(_fmt("Index buy & hold", result.benchmark_metrics))

    d_uni = result.metrics.total_return_pct - result.universe_metrics.total_return_pct
    d_idx = result.metrics.total_return_pct - result.benchmark_metrics.total_return_pct
    d_sharpe = result.metrics.sharpe - result.universe_metrics.sharpe
    d_dd = result.metrics.max_drawdown_pct - result.universe_metrics.max_drawdown_pct

    print(f"\n  vs equal-weight universe: {d_uni:+.1f}% return, {d_sharpe:+.2f} Sharpe, "
          f"{d_dd:+.1f}% drawdown")
    print(f"  vs index:                 {d_idx:+.1f}% return")
    print(f"  turnover {result.turnover_pct:.0f}% of starting capital  ·  "
          f"{len(result.holdings_log)} rebalances")

    if d_uni > 0 and d_sharpe > 0:
        verdict = "SELECTION SKILL — beats holding everything on return and Sharpe"
    elif d_uni > 0:
        verdict = "MIXED — more return than holding everything, but not better Sharpe"
    elif d_dd > 0 and abs(d_uni) < 10:
        verdict = "DEFENSIVE — roughly matches the return with a shallower drawdown"
    else:
        verdict = "NO SKILL — holding the whole universe would have done better"
    print(f"\n  VERDICT: {verdict}")

    if result.holdings_log:
        last_day, last_held = result.holdings_log[-1]
        print(f"  last selection ({last_day.date()}): {', '.join(last_held)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market", nargs="?", choices=["US", "BIST"])
    parser.add_argument("--top", type=int, default=8, help="how many names to hold")
    parser.add_argument("--rebalance", type=int, default=63,
                        help="trading days between rebalances (63 = quarterly)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    hold = HoldConfig(top_n=args.top, rebalance_days=args.rebalance)
    provider = YahooProvider()

    markets = [Market(args.market)] if args.market else [Market.US, Market.BIST]
    for market in markets:
        try:
            run_one(market, provider, hold)
        except Exception as exc:  # noqa: BLE001 - report and continue to next market
            print(f"\n{market.value}: failed: {exc}")

    print("\nThe equal-weight column is the one that matters. Both it and the strategy "
          "\ndraw from the same survivorship-biased universe, so the gap between them "
          "\nis selection skill; the gap to the index is mostly the universe itself.")
    print("No stops are used here by design — being shaken out is what destroys a hold "
          "\nstrategy's return.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
