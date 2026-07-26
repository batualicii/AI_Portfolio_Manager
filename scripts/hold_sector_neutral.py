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

Requires `python -m scripts.build_pit_universe` and `python -m scripts.build_sector_map`.

Usage:
    python -m scripts.hold_sector_neutral
    python -m scripts.hold_sector_neutral --max-per-sector 3 --from-year 2017
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import json
import logging
import pathlib

from src.backtest.hold_engine import HoldBacktester, HoldConfig, sector_capped_selector
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
    args = parser.parse_args()

    for path, cmd in ((PIT, "build_pit_universe"), (SECTORS, "build_sector_map")):
        if not path.exists():
            print(f"missing {path.relative_to(ROOT)} — run "
                  f"`python -m scripts.{cmd}` first")
            return 1

    logging.basicConfig(level=logging.WARNING)
    pit = json.loads(PIT.read_text())
    by_year = {int(y): set(v) for y, v in pit["by_year"].items()}
    smap_doc = json.loads(SECTORS.read_text())
    sectors: dict[str, str] = smap_doc["sectors"]

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
    capped = sector_capped_selector(sectors, max_per_sector=args.max_per_sector)

    print(f"\n{'=' * 88}\nWas it stock selection, or was it one sector?\n{'=' * 88}")
    print(f"  universe : {len(ever_syms)} names, point-in-time membership")
    print(f"  sectors  : {covered}/{len(ever_syms)} mapped "
          f"({covered / len(ever_syms) * 100:.0f}%)")
    print(f"  cap      : at most {args.max_per_sector} of {args.top} names per sector")
    print("  Universe coverage understates the real figure: a name too delisted for "
          "\n  Yahoo to sector usually has no price history either, so it never reaches "
          "\n  the ranking. The number that decides whether the cap is meaningful is "
          "\n  the Unknown share of the book, reported at the end.")

    print(f"\n  {'Year':<6} {'uncapped':>9} {'capped':>9} {'PIT EW':>9} │ "
          f"{'edge':>7} {'edge':>7} {'Δ':>7}   top sector in book")
    print(f"  {'':<6} {'':>9} {'':>9} {'':>9} │ {'unc.':>7} {'cap.':>7} {'':>7}")
    print("  " + "-" * 84)

    raw_edges, cap_edges = [], []
    book: collections.Counter[str] = collections.Counter()
    for year in range(args.from_year, args.to_year + 1):
        window = dict(trade_start=f"{year}-01-01", trade_end=f"{year}-12-31")
        cfg = dataclasses.replace(SignalConfig(), us_universe=ever_syms)
        run = dict(cfg=cfg, hold=dataclasses.replace(hold, **window),
                   members_at=members_at)

        try:
            unc = HoldBacktester(Market.US, provider, **run).run()
            cap = HoldBacktester(Market.US, provider, selector=capped, **run).run()
        except Exception as exc:  # noqa: BLE001
            print(f"  {year:<7} skipped: {str(exc)[:56]}")
            continue

        ew = unc.universe_metrics.total_return_pct
        u, c = unc.metrics.total_return_pct, cap.metrics.total_return_pct
        raw_edges.append(u - ew)
        cap_edges.append(c - ew)

        # How concentrated was the unconstrained book, in its own words?
        counts: collections.Counter[str] = collections.Counter()
        for _, names in unc.holdings_log:
            for sym in names:
                counts[sectors.get(sym) or "Unknown"] += 1
        book.update(counts)
        held = sum(counts.values())
        top = counts.most_common(1)[0] if counts else ("—", 0)
        share = f"{top[0][:22]} {top[1] / held * 100:.0f}%" if held else "—"

        print(f"  {year:<6} {u:>8.1f}% {c:>8.1f}% {ew:>8.1f}% │ "
              f"{u - ew:>+6.1f}% {c - ew:>+6.1f}% {(c - u):>+6.1f}%   {share}")

    if not raw_edges:
        return 1

    print("  " + "-" * 84)
    n = len(raw_edges)
    avg_raw = sum(raw_edges) / n
    avg_cap = sum(cap_edges) / n
    wins_raw = sum(1 for e in raw_edges if e > 0)
    wins_cap = sum(1 for e in cap_edges if e > 0)
    print(f"  uncapped: {avg_raw:+.1f} pp/year, beat EW {wins_raw}/{n} years")
    print(f"  capped  : {avg_cap:+.1f} pp/year, beat EW {wins_cap}/{n} years")

    slots = sum(book.values())
    if slots:
        unknown_share = book.get("Unknown", 0) / slots * 100
        print(f"\n  sector mix of the uncapped book, all years "
              f"({slots} position-slots):")
        for sector, hits in book.most_common(6):
            print(f"    {sector[:28]:<30} {hits / slots * 100:>5.1f}%")
        print(f"  Unknown share: {unknown_share:.1f}%", end="")
        if unknown_share > 15:
            print("  ⚠️  high enough that the cap is partly "
                  "\n  throttling unmapped names as one group rather than diversifying — "
                  "\n  fill the sector map before reading the verdict below.")
        else:
            print("  — low enough that the cap is doing what it says.")

    survived = avg_raw > 0 and avg_cap >= avg_raw * 0.5 and wins_cap >= n * 0.6
    print()
    if survived:
        print("  The edge SURVIVES the cap. Spreading the book across industries keeps "
              "\n  most of it, so the ranking is doing something beyond riding one "
              "\n  sector. That is not yet proof it is tradeable — check the year "
              "\n  spread above — but the sector explanation is now excluded.")
    elif avg_cap > 0:
        print("  The edge SHRINKS under the cap. Part of it was the sector bet. What "
              "\n  remains is small enough to be inside the noise at this sample size; "
              "\n  it is not evidence of company-level selection skill.")
    else:
        print("  The edge DISAPPEARS under the cap. The return came from concentrating "
              "\n  in one trending sector, not from picking companies. A momentum rule "
              "\n  that is only allowed to spread out has no measurable advantage over "
              "\n  simply holding the index members equally.")

    print("\n  Note the cap cuts both ways: it also removes the *downside* of a sector "
          "\n  reversal, so a capped leg that loses less in a bad year is the constraint "
          "\n  working, not the ranking failing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
