"""Catch the trend earlier, or keep buying it late?

The goal is to beat the index, and the stated way to do it is to own a company
while it is still climbing rather than after it has gone vertical. The ranking
tested so far does the opposite by construction: 12-1 momentum buys whatever rose
most over the past year, which means it buys trends at their most extended.

This tests whether adding "and not already extended" helps. Extension is measured
the plainest way available — how far the price sits above its 200-day average:

    baseline   rank by 12-1 momentum, hold the top N
    early      the same ranking, but a name more than --max-extension above its
               200-day average is not eligible at all

Both legs run in the point-in-time universe with the same sector cap, so exactly
one thing differs between them. `--lookbacks` additionally sweeps the momentum
window, since a shorter window is the other way to see a trend sooner — run that
separately, and read one variable at a time.

What this cannot test: whether a company is well run, or has a large market still
ahead of it. Free historical fundamentals do not exist (SPEC section 6c), so the
company-quality half of "own it before the move" is absent here, not measured.
The price-based half is what this decides.

Requires `build_pit_universe` and `build_sector_map`.

Usage:
    python -m scripts.hold_early_trend
    python -m scripts.hold_early_trend --max-extension 0.15
    python -m scripts.hold_early_trend --lookbacks 63,126,252
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import statistics

import pandas as pd

from src.backtest.hold_engine import (
    HoldBacktester,
    HoldConfig,
    momentum_over,
    not_yet_extended,
    sector_capped_selector,
)
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIT = ROOT / "universes" / "sp500_pit.json"
SECTORS = ROOT / "universes" / "sectors.json"


def _summary(label: str, edges: list[float], n: int) -> float:
    mean = sum(edges) / len(edges)
    print(f"    {label:<26} mean {mean:>+6.1f} pp   "
          f"median {statistics.median(edges):>+6.1f} pp   "
          f"beat EW {sum(1 for e in edges if e > 0)}/{n}")
    return mean


def _era_split(label: str, years: list[int], edges: list[float], tail: int = 3) -> None:
    """Where the record actually comes from — a regime should not hide in a mean."""
    if len(years) < tail * 2:
        return
    cut = years[-tail]
    early = [e for y, e in zip(years, edges) if y < cut]
    late = [e for y, e in zip(years, edges) if y >= cut]
    print(f"    {label:<26} {years[0]}-{cut - 1}: "
          f"{sum(early) / len(early):>+6.1f} pp/yr   ·   "
          f"{cut}-{years[-1]}: {sum(late) / len(late):>+6.1f} pp/yr")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--max-per-sector", type=int, default=2)
    parser.add_argument("--max-extension", type=float, default=0.25,
                        help="skip names more than this fraction above their 200d avg")
    parser.add_argument("--ma-window", type=int, default=200)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--from-year", type=int, default=2017)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--lookbacks", default="",
                        help="comma-separated momentum windows in bars, e.g. 63,126,252")
    args = parser.parse_args()

    for path, cmd in ((PIT, "build_pit_universe"), (SECTORS, "build_sector_map")):
        if not path.exists():
            print(f"missing {path.relative_to(ROOT)} — run "
                  f"`python -m scripts.{cmd}` first")
            return 1

    logging.basicConfig(level=logging.WARNING)
    by_year = {int(y): set(v) for y, v in json.loads(PIT.read_text())["by_year"].items()}
    sectors: dict[str, str] = json.loads(SECTORS.read_text())["sectors"]

    ever: set[str] = set()
    for members in by_year.values():
        ever.update(members)
    ever_syms = tuple(sorted(ever))

    def members_at(day) -> set[str]:
        year = day.year if hasattr(day, "year") else dt.date.today().year
        while year not in by_year and year > min(by_year):
            year -= 1
        return by_year.get(year, set())

    provider = YahooProvider()
    hold = HoldConfig(top_n=args.top, period=args.period)
    capped = sector_capped_selector(sectors, max_per_sector=args.max_per_sector)

    legs: dict[str, object] = {
        "baseline": None,  # engine default: 12-1 momentum
        "early": not_yet_extended(args.max_extension, args.ma_window),
    }
    lookbacks = [int(x) for x in args.lookbacks.split(",") if x.strip()]
    for bars in lookbacks:
        legs[f"mom {bars}d"] = momentum_over(bars)

    print(f"\n{'=' * 96}\nCatch the trend earlier, or keep buying it late?\n{'=' * 96}")
    print(f"  universe   : {len(ever_syms)} names, point-in-time membership")
    print(f"  every leg  : top {args.top}, max {args.max_per_sector} per sector, "
          f"quarterly, no stops")
    print(f"  early      : skip anything more than {args.max_extension:.0%} above its "
          f"{args.ma_window}-day average")
    print("""
  12-1 momentum ranks by what has already risen most over a year, so on its own it
  buys trends at their most extended — the opposite of owning a company before the
  move. The `early` leg keeps the same ranking and removes the names that have
  already gone vertical. If that helps, the entry point was costing return; if it
  hurts, extension was a feature and staying with strength is the better rule.

  Every leg carries the same warm-up and the same dates, so the comparison is of
  the rule and not of the window it was given.""")

    names = list(legs)
    header = ("  Year   " + " ".join(f"{k:>10}" for k in names)
              + f" │ {'PIT EW':>9}   held / mean extension")
    print("\n" + header)
    print("  " + "-" * (len(header) - 2))

    edges: dict[str, list[float]] = {k: [] for k in names}
    years_run: list[int] = []
    risk: dict[str, list[tuple[float, float]]] = {k: [] for k in names}
    ew_risk: list[tuple[float, float]] = []
    partial: set[int] = set()

    for year in range(args.from_year, args.to_year + 1):
        cfg = dataclasses.replace(SignalConfig(), us_universe=ever_syms)
        run = dict(cfg=cfg, members_at=members_at, selector=capped,
                   hold=dataclasses.replace(
                       hold, trade_start=f"{year}-01-01", trade_end=f"{year}-12-31"))
        try:
            results = {
                name: HoldBacktester(Market.US, provider, scorer=scorer, **run).run()
                for name, scorer in legs.items()
            }
        except Exception as exc:  # noqa: BLE001
            print(f"  {year:<7} skipped: {str(exc)[:56]}")
            continue

        ew = results["baseline"].universe_metrics.total_return_pct
        years_run.append(year)
        for name, r in results.items():
            edges[name].append(r.metrics.total_return_pct - ew)
            risk[name].append((r.metrics.max_drawdown_pct, r.metrics.sharpe))
        um = results["baseline"].universe_metrics
        ew_risk.append((um.max_drawdown_pct, um.sharpe))

        last = results["baseline"].equity.index[-1]
        if (pd.Timestamp(f"{year}-12-31").tz_localize(last.tz) - last).days > 45:
            partial.add(year)

        # How many distinct names the early leg could find, and how extended the
        # baseline's picks were — the mechanism, not just the outcome.
        early_names = {s for _, held in results["early"].holdings_log for s in held}
        base_names = {s for _, held in results["baseline"].holdings_log for s in held}
        overlap = len(early_names & base_names)

        cells = " ".join(f"{results[k].metrics.total_return_pct:>9.1f}%" for k in names)
        mark = "†" if year in partial else " "
        print(f"  {year:<5}{mark}{cells} │ {ew:>8.1f}%   "
              f"{len(early_names):>2} vs {len(base_names):>2}, "
              f"{overlap} shared")

    if not years_run:
        return 1

    n = len(years_run)
    print("  " + "-" * (len(header) - 2))
    print(f"\n  edge over point-in-time equal-weight, {years_run[0]}-{years_run[-1]}:")
    means = {k: _summary(k, edges[k], n) for k in names}

    if partial:
        print(f"\n  † {', '.join(str(y) for y in sorted(partial))}: partial year, "
              f"fewer months than every other row.")

    print("\n  where each rule's record lives:")
    for k in names:
        _era_split(k, years_run, edges[k])

    print("\n  risk vs the passive alternative (mean over all years):")
    print(f"    {'rule':<26} {'maxDD':>8} {'Sharpe':>8}")
    for k in names:
        dd = sum(r[0] for r in risk[k]) / n
        sh = sum(r[1] for r in risk[k]) / n
        print(f"    {k:<26} {dd:>7.1f}% {sh:>8.2f}")
    print(f"    {'point-in-time equal-wt':<26} "
          f"{sum(r[0] for r in ew_risk) / n:>7.1f}% "
          f"{sum(r[1] for r in ew_risk) / n:>8.2f}")

    print()
    gain = means["early"] - means["baseline"]
    if gain > 2:
        print(f"  Entering earlier HELPED by {gain:+.1f} pp/year. Buying strength after "
              "\n  it has gone vertical was costing return, and the fix is a filter "
              "\n  rather than a new signal. Check the era split before believing it: "
              "\n  an improvement that lives in one stretch is still one regime.")
    elif gain > -2:
        print(f"  Entering earlier changed almost nothing ({gain:+.1f} pp/year). The "
              "\n  extension filter removes names the ranking would have bought without "
              "\n  replacing the return they brought, so on this evidence the entry "
              "\n  point is not where the money was won or lost.")
    else:
        print(f"  Entering earlier HURT by {gain:+.1f} pp/year. In this universe the "
              "\n  extended names kept going — extension was a feature of the winners, "
              "\n  not a warning. That is momentum behaving exactly as documented, and "
              "\n  it argues for staying with strength rather than trying to be early.")

    print("\n  What none of these legs can answer: whether the company was any good. "
          "\n  Every rule here reads price only. 'The next NVDA' is a claim about a "
          "\n  business, and free data does not carry the history needed to test it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
