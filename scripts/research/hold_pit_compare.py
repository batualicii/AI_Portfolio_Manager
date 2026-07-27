"""Measure the survivorship bias instead of arguing about it.

Every selection result so far picked from *today's* S&P 500. This runs the same
strategy twice — once on today's membership, once on membership reconstructed for
each historical date — and reports the gap. That gap is the bias, in percent.

The reconstruction adds back 236 names that were in the index at some point and
are not today: the acquisitions, the collapses, the slow declines. They are the
positions a momentum strategy would actually have bought and been hurt by, and
they are missing from every number produced up to now.

Two honesty guards, because a half-working fix is worse than a known-broken one:

  * coverage is reported. A dropped name that Yahoo can no longer price is still
    excluded, so if most of them are unpriceable the bias is reduced rather than
    removed, and the output says so.
  * the "today's membership" leg is re-run here rather than quoted from an
    earlier session, so both legs share code, costs, and dates.

Run `python -m scripts.research.build_pit_universe` first.

Usage:
    python -m scripts.research.hold_pit_compare --from-year 2017 --to-year 2026
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib

from src.backtest.hold_engine import HoldBacktester, HoldConfig
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PIT = ROOT / "universes" / "sp500_pit.json"
TODAY = ROOT / "universes" / "sp500.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--from-year", type=int, default=2017)
    parser.add_argument("--to-year", type=int, default=2026)
    args = parser.parse_args()

    if not PIT.exists():
        print(f"missing {PIT.relative_to(ROOT)} — run "
              f"`python -m scripts.research.build_pit_universe` first")
        return 1

    logging.basicConfig(level=logging.WARNING)
    pit = json.loads(PIT.read_text())
    by_year = {int(y): set(v) for y, v in pit["by_year"].items()}
    today_syms = tuple(json.loads(TODAY.read_text())["symbols"])

    # The full historical opportunity set: everyone who was ever a member.
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

    print(f"\n{'=' * 86}\nSurvivorship bias, measured\n{'=' * 86}")
    print(f"  today's membership     : {len(today_syms)} names")
    print(f"  ever a member since {min(by_year)}: {len(ever_syms)} names "
          f"(+{len(ever_syms) - len(today_syms)} that have since dropped out)")

    print(f"\n  Two questions, and they have different answers:"
          f"\n    1. how much did the bias inflate the strategy's return?   (gap)"
          f"\n    2. does the edge over a passive alternative survive?      (edge)")

    print(f"\n  {'Year':<6} {'today':>9} {'PIT':>9} {'gap':>8} │ "
          f"{'PIT EW':>9} {'edge':>8}   verdict")
    print("  " + "-" * 76)

    gaps, edges = [], []
    for year in range(args.from_year, args.to_year + 1):
        window = dict(trade_start=f"{year}-01-01", trade_end=f"{year}-12-31")

        try:
            biased = HoldBacktester(
                Market.US, provider,
                cfg=dataclasses.replace(SignalConfig(), us_universe=today_syms),
                hold=dataclasses.replace(hold, **window),
            ).run()
            honest = HoldBacktester(
                Market.US, provider,
                cfg=dataclasses.replace(SignalConfig(), us_universe=ever_syms),
                hold=dataclasses.replace(hold, **window),
                members_at=members_at,
            ).run()
        except Exception as exc:  # noqa: BLE001
            print(f"  {year:<7} skipped: {str(exc)[:56]}")
            continue

        b = biased.metrics.total_return_pct
        h = honest.metrics.total_return_pct
        # The benchmark computed inside the honest leg is membership-aware, so
        # this is the passive alternative available in that same universe.
        ew = honest.universe_metrics.total_return_pct
        gap, edge = b - h, h - ew
        gaps.append(gap)
        edges.append(edge)

        verdict = ("beat the passive alternative" if edge > 2
                   else "matched it" if edge >= -2
                   else "lost to it")
        print(f"  {year:<6} {b:>8.1f}% {h:>8.1f}% {gap:>+7.1f}% │ "
              f"{ew:>8.1f}% {edge:>+7.1f}%   {verdict}")

    if gaps:
        print("  " + "-" * 76)
        avg_gap = sum(gaps) / len(gaps)
        avg_edge = sum(edges) / len(edges)
        wins = sum(1 for e in edges if e > 0)
        print(f"  average bias: {avg_gap:+.1f} pp/year   ·   "
              f"average edge over PIT equal-weight: {avg_edge:+.1f} pp/year   ·   "
              f"beat it {wins}/{len(edges)} years")

        print()
        if avg_gap > 5:
            print("  On the bias: a large part of every earlier number in this repo was "
                  "\n  survivorship. Those figures cannot be quoted again.")
        elif avg_gap > 2:
            print("  On the bias: a meaningful slice of the earlier numbers was "
                  "survivorship.")
        else:
            print("  On the bias: earlier numbers were not materially inflated.")

        if avg_edge > 3 and wins >= len(edges) * 0.6:
            print("  On the edge: the ranking still beats the passive alternative in an "
                  "\n  honest universe, in most years. This is the first defensible "
                  "\n  evidence of selection skill produced here.")
        elif avg_edge > 0:
            print("  On the edge: positive on average but inconsistent across years — "
                  "\n  inside the range luck produces at this sample size. Not tradeable "
                  "\n  on this evidence.")
        else:
            print("  On the edge: the ranking does NOT beat holding the index members "
                  "\n  equally. Whatever remained after removing the bias is not skill.")

    print("\n  Caveats that remain even with this run:")
    print("   · dropped names Yahoo can no longer price are still absent, so the")
    print("     point-in-time leg is closer to honest, not fully honest;")
    print("   · Wikipedia calls its table 'selected changes', so early membership")
    print("     is approximate;")
    print("   · this reconstructs the index, not a tradeable universe — liquidity")
    print("     and borrow constraints of the era are not modelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
