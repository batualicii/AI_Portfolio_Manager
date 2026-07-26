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

Run `python -m scripts.build_pit_universe` first.

Usage:
    python -m scripts.hold_pit_compare --from-year 2017 --to-year 2026
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

ROOT = pathlib.Path(__file__).resolve().parent.parent
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
              f"`python -m scripts.build_pit_universe` first")
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

    print(f"\n  {'Year':<7} {'today-only':>12} {'point-in-time':>15} {'gap':>9}"
          f"   what the gap means")
    print("  " + "-" * 78)

    gaps = []
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
        gap = b - h
        gaps.append(gap)
        note = ("bias inflated the result" if gap > 2
                else "no meaningful bias" if abs(gap) <= 2
                else "point-in-time did better")
        print(f"  {year:<7} {b:>11.1f}% {h:>14.1f}% {gap:>+8.1f}%   {note}")

    if gaps:
        print("  " + "-" * 78)
        avg = sum(gaps) / len(gaps)
        print(f"  average gap: {avg:+.1f} percentage points per year")
        if avg > 5:
            print("\n  VERDICT: a large part of the reported edge was survivorship bias. "
                  "\n  Every earlier number in this repo overstates the strategy.")
        elif avg > 2:
            print("\n  VERDICT: bias accounts for a meaningful slice of the edge. "
                  "\n  Earlier numbers need discounting, but something remains.")
        else:
            print("\n  VERDICT: the edge largely survives an honest universe. That is "
                  "\n  the strongest evidence available here — though see the caveats.")

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
