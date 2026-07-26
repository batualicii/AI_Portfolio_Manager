"""Was it stock selection, or was it one sector?

After the point-in-time run, the edge over a passive alternative averaged
+11.7 pp/year — but ~92% of it came from 2024 and 2026, and both sit inside the
same semiconductor/AI move. That leaves one obvious explanation unexcluded: an
unconstrained 12-1 momentum ranking will fill the whole book with whatever sector
is trending, so the "edge" may be a sector bet wearing a selection rule's clothes.

This runs both versions side by side in the same point-in-time universe:

    uncapped   the ranking as tested so far — top N by momentum, no constraints
    capped     the same ranking, at most `--max-per-sector` names from one sector

Read it this way:

  * capped edge holds up  -> the ranking picks companies, not industries. The
    concentration was a side effect, not the source.
  * capped edge collapses -> the result was the sector. Removing the bet removes
    the return, and there is nothing left to trade.

The book's sector composition is printed too, so the concentration is visible as
a number rather than inferred from the outcome.

**Incomplete sector coverage does not block this run.** Roughly a fifth of the
historical universe cannot be sectored, because a company delisted hard enough to
lose its ticker also loses its data — and those are exactly the dropouts that
point-in-time membership put back. Handling them one way or another silently
decides the answer, so the capped leg runs under all three handlings at once and
the verdict is only reported as clean when they agree. A split verdict means the
coverage gap is deciding, not the strategy, and it says so.

Requires `python -m scripts.build_pit_universe` and `python -m scripts.build_sector_map`.

Usage:
    python -m scripts.hold_sector_neutral                    # brackets all three
    python -m scripts.hold_sector_neutral --unknown own      # one handling only
    python -m scripts.hold_sector_neutral --max-per-sector 3
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import statistics

from src.backtest.hold_engine import (
    UNKNOWN_POLICIES,
    HoldBacktester,
    HoldConfig,
    sector_capped_selector,
)
from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIT = ROOT / "universes" / "sp500_pit.json"
SECTORS = ROOT / "universes" / "sectors.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--max-per-sector", type=int, default=2)
    parser.add_argument("--period", default="10y")
    parser.add_argument("--from-year", type=int, default=2017)
    parser.add_argument("--to-year", type=int, default=2026)
    parser.add_argument("--unknown", default="own,shared,exclude",
                        help="comma-separated handling of unmapped symbols: "
                             "own | shared | exclude (see sector_capped_selector)")
    args = parser.parse_args()

    policies = [p.strip() for p in args.unknown.split(",") if p.strip()]
    for policy in policies:
        if policy not in UNKNOWN_POLICIES:
            print(f"unknown policy {policy!r}; pick from {', '.join(UNKNOWN_POLICIES)}")
            return 1

    for path, cmd in ((PIT, "build_pit_universe"), (SECTORS, "build_sector_map")):
        if not path.exists():
            print(f"missing {path.relative_to(ROOT)} — run "
                  f"`python -m scripts.{cmd}` first")
            return 1

    logging.basicConfig(level=logging.WARNING)
    pit = json.loads(PIT.read_text())
    by_year = {int(y): set(v) for y, v in pit["by_year"].items()}
    sectors: dict[str, str] = json.loads(SECTORS.read_text())["sectors"]

    ever: set[str] = set()
    for members in by_year.values():
        ever.update(members)
    ever_syms = tuple(sorted(ever))
    covered = sum(1 for s in ever_syms if s in sectors)

    def members_at(day) -> set[str]:
        year = day.year if hasattr(day, "year") else dt.date.today().year
        while year not in by_year and year > min(by_year):
            year -= 1
        return by_year.get(year, set())

    provider = YahooProvider()
    hold = HoldConfig(top_n=args.top, period=args.period)

    print(f"\n{'=' * 92}\nWas it stock selection, or was it one sector?\n{'=' * 92}")
    print(f"  universe : {len(ever_syms)} names, point-in-time membership")
    print(f"  sectors  : {covered}/{len(ever_syms)} mapped "
          f"({covered / len(ever_syms) * 100:.0f}%)")
    print(f"  cap      : at most {args.max_per_sector} of {args.top} names per sector")
    print(f"  unmapped : {', '.join(policies)}")
    print("""
  The names that cannot be sectored are almost exactly the ones that dropped out of
  the index — the group point-in-time membership exists to put back — so how they
  are handled tilts the answer in a knowable direction. Rather than pick one and
  defend it, all three run and bracket the result:

    exclude  unmapped names cannot be held. Re-deletes the dropouts, which is the
             original survivorship bias.            -> OPTIMISTIC bound
    own      unmapped names are exempt from the cap. The cap still bites where the
             suspicion is, dropouts are left alone. -> the honest middle
    shared   unmapped names compete for one bucket. Suppresses exposure to the
             honest half of the universe.           -> PESSIMISTIC bound

  If the verdict is the same under all three, the coverage gap did not decide it.""")

    rows: dict[str, list[float]] = {p: [] for p in policies}
    raw_edges: list[float] = []
    years_run: list[int] = []
    book: collections.Counter[str] = collections.Counter()

    header = f"  {'Year':<6} {'uncapped':>9} " + " ".join(
        f"{p:>9}" for p in policies) + f" │ {'PIT EW':>9}   top sector in book"
    print("\n" + header)
    print("  " + "-" * (len(header) - 2))

    for year in range(args.from_year, args.to_year + 1):
        window = dict(trade_start=f"{year}-01-01", trade_end=f"{year}-12-31")
        cfg = dataclasses.replace(SignalConfig(), us_universe=ever_syms)
        run = dict(cfg=cfg, hold=dataclasses.replace(hold, **window),
                   members_at=members_at)

        try:
            unc = HoldBacktester(Market.US, provider, **run).run()
            capped = {
                policy: HoldBacktester(
                    Market.US, provider,
                    selector=sector_capped_selector(
                        sectors, max_per_sector=args.max_per_sector,
                        unknown_policy=policy),
                    **run,
                ).run()
                for policy in policies
            }
        except Exception as exc:  # noqa: BLE001
            print(f"  {year:<7} skipped: {str(exc)[:56]}")
            continue

        ew = unc.universe_metrics.total_return_pct
        u = unc.metrics.total_return_pct
        years_run.append(year)
        raw_edges.append(u - ew)
        for policy, result in capped.items():
            rows[policy].append(result.metrics.total_return_pct - ew)

        counts: collections.Counter[str] = collections.Counter()
        for _, names in unc.holdings_log:
            for sym in names:
                counts[sectors.get(sym) or "Unknown"] += 1
        book.update(counts)
        held = sum(counts.values())
        top = counts.most_common(1)[0] if counts else ("—", 0)
        share = f"{top[0][:22]} {top[1] / held * 100:.0f}%" if held else "—"

        cells = " ".join(f"{capped[p].metrics.total_return_pct:>8.1f}%"
                         for p in policies)
        print(f"  {year:<6} {u:>8.1f}% {cells} │ {ew:>8.1f}%   {share}")

    if not years_run:
        return 1

    n = len(years_run)
    print("  " + "-" * (len(header) - 2))
    print(f"\n  edge over point-in-time equal-weight, {years_run[0]}-{years_run[-1]}:")

    def summarise(label: str, edges: list[float]) -> tuple[float, int]:
        mean = sum(edges) / len(edges)
        median = statistics.median(edges)
        wins = sum(1 for e in edges if e > 0)
        print(f"    {label:<22} mean {mean:>+6.1f} pp   median {median:>+6.1f} pp   "
              f"beat EW {wins}/{n}")
        return mean, wins

    raw_mean, _ = summarise("uncapped", raw_edges)
    cap_means = {p: summarise(f"capped ({p})", rows[p]) for p in policies}

    slots = sum(book.values())
    if slots:
        unknown_share = book.get("Unknown", 0) / slots * 100
        print(f"\n  sector mix of the uncapped book, all years "
              f"({slots} position-slots):")
        for sector, hits in book.most_common(6):
            print(f"    {sector[:28]:<30} {hits / slots * 100:>5.1f}%")
        print(f"    {'(unmapped)':<30} {unknown_share:>5.1f}%")

    # The verdict is only clean when every policy agrees.
    verdicts = set()
    for policy, (mean, wins) in cap_means.items():
        if raw_mean > 0 and mean >= raw_mean * 0.5 and wins >= n * 0.6:
            verdicts.add("survives")
        elif mean > 0:
            verdicts.add("shrinks")
        else:
            verdicts.add("gone")

    print()
    if len(verdicts) > 1:
        print("  SPLIT VERDICT — the unmapped-symbol handling changes the answer "
              f"({', '.join(sorted(verdicts))}). The sector map has to be filled in "
              "\n  before this test can decide anything; the coverage gap is doing the "
              "\n  deciding right now, not the strategy.")
    elif verdicts == {"survives"}:
        print("  The edge SURVIVES the cap under every unmapped-name policy. Spreading "
              "\n  the book across industries keeps most of it, so the ranking is doing "
              "\n  something beyond riding one sector. Not proof it is tradeable — check "
              "\n  the year spread — but the sector explanation is now excluded.")
    elif verdicts == {"shrinks"}:
        print("  The edge SHRINKS under the cap, under every policy. Part of it was the "
              "\n  sector bet. What remains is small enough to sit inside the noise at "
              "\n  this sample size; it is not evidence of company-level selection skill.")
    else:
        print("  The edge DISAPPEARS under the cap, under every policy. The return came "
              "\n  from concentrating in one trending sector, not from picking companies. "
              "\n  A momentum rule that must spread out has no measurable advantage over "
              "\n  holding the index members equally.")

    print("\n  Note the cap cuts both ways: it also removes the *downside* of a sector "
          "\n  reversal, so a capped leg that loses less in a bad year is the constraint "
          "\n  working, not the ranking failing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
