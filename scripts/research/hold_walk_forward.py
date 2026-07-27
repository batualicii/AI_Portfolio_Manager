"""Run the selection strategy year by year, over a window long enough to hurt it.

The first selection run covered 2022-05 onward and returned 79% CAGR. That window
is one sustained trend — the AI/semiconductor move — and it begins *after* the
January 2022 momentum crash. Momentum's documented behaviour is that it compounds
beautifully through a trend and then loses 30-60% at a sharp reversal, so a test
that contains only the good half is not a test.

This pulls a longer history (10y by default) and reports every calendar year
separately against the same two references. What to look for:

  * a year where the strategy loses badly while the index does not — that is
    momentum's failure mode, and it needs to be survivable, not absent;
  * consistency. A strategy that wins 8 years by a little is a different object
    from one that wins once by a lot and the rest by nothing.

Survivorship bias gets *worse* the further back this reaches: the universe is
today's index membership, so 2016's real opportunity set is not what is being
tested. The crash behaviour is still informative — if the strategy collapses even
on a universe rigged in its favour, that settles it.

Usage:
    python -m scripts.research.hold_walk_forward US
    python -m scripts.research.hold_walk_forward US --period 10y --top 8
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib

from src.backtest.hold_engine import HoldBacktester, HoldConfig
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SP500 = ROOT / "universes" / "sp500.json"


def _universe(market: Market) -> tuple[tuple[str, ...], str]:
    if market is Market.US and SP500.exists():
        data = json.loads(SP500.read_text())
        return tuple(data["symbols"]), f"{data['name']} ({data['fetched_at']})"
    cfg = SignalConfig()
    return cfg.universe(market), "built-in watchlist"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market", nargs="?", default="US", choices=["US", "BIST"])
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--period", default="10y", help="how much history to pull")
    parser.add_argument("--from-year", type=int, default=2017)
    parser.add_argument("--to-year", type=int, default=2026)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    market = Market(args.market)
    symbols, source = _universe(market)
    cfg = dataclasses.replace(SignalConfig(), **(
        {"us_universe": symbols} if market is Market.US else {"bist_universe": symbols}
    ))
    provider = YahooProvider()

    print(f"\n{'=' * 88}\n{market.value} — selection strategy, year by year"
          f"\nuniverse: {source} ({len(symbols)} names), holding {args.top}, "
          f"history {args.period}\n{'=' * 88}")
    print(f"  {'Year':<7} {'Strategy':>10} {'Equal-wt':>10} {'Index':>9} "
          f"{'vs EW':>8} {'Sharpe':>7} {'maxDD':>8}   flag")
    print("  " + "-" * 84)

    beat_ew = beat_idx = losing_years = total = 0
    worst_year = None

    for year in range(args.from_year, args.to_year + 1):
        hold = HoldConfig(top_n=args.top, period=args.period,
                          trade_start=f"{year}-01-01", trade_end=f"{year}-12-31")
        try:
            r = HoldBacktester(market, provider, cfg=cfg, hold=hold).run()
        except Exception as exc:  # noqa: BLE001
            print(f"  {year:<7} skipped: {str(exc)[:60]}")
            continue

        strat = r.metrics.total_return_pct
        ew = r.universe_metrics.total_return_pct
        idx = r.benchmark_metrics.total_return_pct
        total += 1
        beat_ew += strat > ew
        beat_idx += strat > idx
        if strat < 0:
            losing_years += 1
        if worst_year is None or strat < worst_year[1]:
            worst_year = (year, strat, idx)

        # Flag the case that matters: the strategy down while the market is not.
        flag = ""
        if strat < 0 and idx > 0:
            flag = "⚠️ lost while market rose"
        elif strat < idx - 10:
            flag = "trailed index badly"

        print(f"  {year:<7} {strat:>9.1f}% {ew:>9.1f}% {idx:>8.1f}% "
              f"{strat - ew:>+7.1f}% {r.metrics.sharpe:>7.2f} "
              f"{r.metrics.max_drawdown_pct:>7.1f}%   {flag}")

    if total:
        print("  " + "-" * 84)
        print(f"  beat equal-weight {beat_ew}/{total} years · beat index "
              f"{beat_idx}/{total} · losing years {losing_years}/{total}")
        if worst_year:
            y, s, i = worst_year
            print(f"  worst year: {y} at {s:+.1f}% (index {i:+.1f}%)")

    print("\n  A strategy that wins most years by a little is a different object from "
          "\n  one that wins once by a lot. Check the spread, not just the count.")
    print("  Survivorship bias grows the further back this reaches — the universe is "
          "\n  today's membership, so early years test a rigged opportunity set. If it "
          "\n  breaks even here, that is decisive; if it survives, that is not proof.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
