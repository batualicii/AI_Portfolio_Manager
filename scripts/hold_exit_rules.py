"""Does letting winners run produce the tail, and what does it cost?

Every earlier experiment changed the *entry* — the momentum window, an extension
filter, a sector cap. The exit never moved: a name that fell out of the quarterly
ranking was sold. That rule makes the intended outcome impossible. Quarterly 12-1
momentum would have sold NVDA in early 2019 (after −56%) and again in early 2023
(after −66%), capturing perhaps a third of the move. It exits precisely the
drawdowns a position must survive to compound into something large.

Same universe, same entry signal, same sector cap. Only the exit differs:

    rotate   sell whatever drops out of the ranking, rebalance to equal weight
    trend    hold until the trend is genuinely gone — closes below the 200-day
             average for four straight weeks — and never trim a winner
    forever  buy once, never sell, never rebalance

**This is not measuring an edge, and cannot.** SPEC section 6c: the mean edge of an
8-name book has a standard error of ~4 pp over ten years, so no ranking of these
three by average return would mean anything. What the run measures instead is the
*shape* of the outcome — how much of the result came from one position, how many
went nowhere, and what the drawdown cost of holding through was. Those are
descriptive facts, and descriptive is enough to choose a rule.

The window is run whole rather than year by year on purpose: slicing into calendar
years would force a liquidation every December, which is the exact behaviour being
tested against.

Requires `build_pit_universe` and `build_sector_map`.

Usage:
    python -m scripts.hold_exit_rules
    python -m scripts.hold_exit_rules --top 8 --period 10y
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import statistics

from src.backtest.hold_engine import (
    HoldBacktester,
    HoldConfig,
    PositionOutcome,
    never_exit,
    rank_drop_exit,
    sector_capped_selector,
    trend_break_exit,
)
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIT = ROOT / "universes" / "sp500_pit.json"
SECTORS = ROOT / "universes" / "sectors.json"


def _distribution(name: str, outcomes: list[PositionOutcome]) -> None:
    """What the result was made of — the question an average cannot answer."""
    if not outcomes:
        print(f"    {name:<10} no positions")
        return

    mults = sorted(o.multiple for o in outcomes)
    winners = sorted(outcomes, key=lambda o: o.contribution, reverse=True)
    total_pnl = sum(o.contribution for o in outcomes)
    best = winners[0]
    best_share = (best.contribution / total_pnl * 100) if total_pnl > 0 else float("nan")
    losers = sum(1 for m in mults if m < 1.0)

    print(f"    {name:<10} {len(outcomes):>3} positions   "
          f"median {statistics.median(mults):>5.2f}x   mean {statistics.mean(mults):>5.2f}x   "
          f"≥2x {sum(1 for m in mults if m >= 2):>2}  ≥3x {sum(1 for m in mults if m >= 3):>2}  "
          f"≥5x {sum(1 for m in mults if m >= 5):>2}   losers {losers:>3}")
    print(f"    {'':<10} best: {best.symbol} at {best.multiple:.1f}x, "
          f"{best_share:.0f}% of all profit   ·   "
          f"top 3 = {sum(o.contribution for o in winners[:3]) / total_pnl * 100:.0f}%"
          if total_pnl > 0 else f"    {'':<10} no profit to attribute")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--max-per-sector", type=int, default=2)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--ma-window", type=int, default=200)
    parser.add_argument("--weeks", type=int, default=4)
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
    base = HoldConfig(top_n=args.top, period=args.period)
    cfg = dataclasses.replace(SignalConfig(), us_universe=ever_syms)
    capped = sector_capped_selector(sectors, max_per_sector=args.max_per_sector)

    legs = {
        "rotate": (rank_drop_exit, True),
        "trend": (trend_break_exit(args.ma_window, args.weeks), False),
        "forever": (never_exit, False),
    }

    print(f"\n{'=' * 88}\nDoes letting winners run produce the tail?\n{'=' * 88}")
    print(f"  universe : {len(ever_syms)} names, point-in-time membership")
    print(f"  common   : top {args.top}, max {args.max_per_sector} per sector, "
          f"{args.period} in one unbroken window")
    print(f"  trend    : exit after {args.weeks} weeks fully below the "
          f"{args.ma_window}-day average")
    print("""
  Read the distribution, not the ranking. The average edge of an 8-name book is
  not measurable here (SPEC 6c), so "which rule won" is a question this data
  cannot answer. What it can answer: where the money came from, how many
  positions did nothing, and what holding through cost in drawdown.""")

    results = {}
    for name, (rule, rebalance) in legs.items():
        try:
            results[name] = HoldBacktester(
                Market.US, provider, cfg=cfg, selector=capped, members_at=members_at,
                hold=dataclasses.replace(base, rebalance_weights=rebalance),
                exit_rule=rule,
            ).run()
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: skipped — {str(exc)[:60]}")

    if not results:
        return 1

    any_run = next(iter(results.values()))
    print(f"\n  {'rule':<10} {'return':>9} {'CAGR':>7} {'maxDD':>8} {'Sharpe':>7} "
          f"{'turnover':>9}")
    print("  " + "-" * 56)
    for name, r in results.items():
        m = r.metrics
        print(f"  {name:<10} {m.total_return_pct:>8.1f}% {m.cagr_pct:>6.1f}% "
              f"{m.max_drawdown_pct:>7.1f}% {m.sharpe:>7.2f} {r.turnover_pct:>8.0f}%")
    for label, m in (("PIT equal-wt", any_run.universe_metrics),
                     ("index (B&H)", any_run.benchmark_metrics)):
        print(f"  {label:<10} {m.total_return_pct:>8.1f}% {m.cagr_pct:>6.1f}% "
              f"{m.max_drawdown_pct:>7.1f}% {m.sharpe:>7.2f} {'—':>9}")

    print("\n  what each result was made of:")
    for name, r in results.items():
        _distribution(name, r.outcomes)

    rot, fore = results.get("rotate"), results.get("forever")
    if rot and fore:
        rot_best = max((o.multiple for o in rot.outcomes), default=0.0)
        fore_best = max((o.multiple for o in fore.outcomes), default=0.0)
        print()
        if fore_best > rot_best * 1.5:
            print(f"  Holding produced a bigger single winner ({fore_best:.1f}x vs "
                  f"{rot_best:.1f}x). That is the asymmetry the design exists for, "
                  f"\n  and rotation was cutting it off. Check the drawdown column "
                  f"before\n  celebrating: that is what it cost to sit through.")
        elif fore_best > rot_best:
            print(f"  Holding produced a somewhat bigger winner ({fore_best:.1f}x vs "
                  f"{rot_best:.1f}x),\n  but not the order-of-magnitude difference the "
                  f"argument for it assumes.")
        else:
            print(f"  Holding did NOT produce a bigger winner ({fore_best:.1f}x vs "
                  f"{rot_best:.1f}x).\n  In this universe the names that fell out of the "
                  f"ranking mostly kept falling,\n  and rotation was doing real work.")

    print("\n  Caveats that still apply: the universe is point-in-time but only ~48% of")
    print("  dropped names can be priced, costs are modelled at 0.1% with no per-trade")
    print("  minimum, and one ten-year window of one market is a single sample.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
