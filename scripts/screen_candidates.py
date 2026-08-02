"""A research queue for the pond institutions cannot fish in.

This is **not** a buy list and does not rank by expected return. Two generations
of evidence in this repo say a price-based ranking cannot be shown to work at
this portfolio size (SPEC section 6c), so producing one and calling it a
recommendation would repeat the mistake with a new coat of paint.

What it does instead is narrow several hundred names down to a few dozen worth a
human reading, using the one advantage a small account actually has: **capacity**.
A fund running billions cannot take a meaningful position in a small company
without owning an absurd share of it, so those names carry less analyst coverage
and less institutional ownership. That is where an individual is not competing
against better-resourced people at their own game.

Three filters, each corresponding to a real constraint:

    size        small enough that large funds structurally cannot be here
    liquidity   large enough that *you* can get in and out
    ownership   not already crowded with institutions

**The ownership filter is not sector-neutral, and that matters.** Small banks and
REITs carry less institutional money for structural reasons, so an absolute
ceiling quietly selects for them — the first live run returned a list that was
mostly Financials and Real Estate, which was the filter talking rather than a
finding about those industries. The run now prints the sector mix of survivors
against the universe, and caps the reading list per sector so a structural bias
cannot fill it.

On BIST the filter is **disabled outright**. Yahoo reports a number there, but it
counts US institutional filers only — around 4% for household names, which is
real and measures the wrong thing. Running it would show three filters while two
did the work, and a filter that silently never fires is worse than an absent one:
the output looks like it passed a test it never took.

Then a momentum + quality ordering, purely to decide what to read first. The
output is a reading list. Every name still needs the part no screen can do: work
out whether the business is any good, which is where a small investor's real
edge — knowing something specific — actually lives.

BIST thresholds are deliberately more conservative. Spreads are wider, disclosure
is thinner, and the promoted-microcap problem is worse; a screen tuned for US
liquidity would hand back names that cannot be traded out of.

Usage:
    python -m scripts.build_smallcap_universe      # US pond
    python -m scripts.build_bist_universe          # BIST pond
    python -m scripts.screen_candidates US
    python -m scripts.screen_candidates BIST
"""
from __future__ import annotations

import argparse
import logging
import pathlib

from src.market.yahoo import YahooProvider
from src.models import Market
from src.screen.candidates import (
    LIMITS,
    Candidate,
    Thresholds,
    load_universe,
    screen,
)

__all__ = ["LIMITS", "Candidate", "Thresholds", "main"]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market", nargs="?", default="US", choices=["US", "BIST"])
    parser.add_argument("--universe", type=pathlib.Path, default=None)
    parser.add_argument("--top", type=int, default=25, help="how many to read first")
    parser.add_argument("--max-per-sector", type=int, default=4,
                        help="cap the reading list per sector, so a filter's "
                             "structural bias cannot fill it with one industry")
    parser.add_argument("--period", default="2y")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    market = Market(args.market)
    limits = LIMITS[market]
    symbols, sectors, source = load_universe(market, args.universe)

    provider = YahooProvider()
    fx = provider.get_fx_rate("USD", "TRY") if market is Market.BIST else 1.0
    if market is Market.BIST and not fx:
        print("No USD/TRY rate available — cannot compare BIST sizes to a USD band "
              "without one, and a fixed TRY threshold would age badly. Aborting "
              "rather than screening on a number that means nothing.")
        return 1

    print(f"\n{'=' * 88}\n{market.value} — research queue, not a buy list\n{'=' * 88}")
    print(f"  universe  : {source} ({len(symbols)} names)")
    print(f"  size      : ${limits.min_cap_usd / 1e6:,.0f}M – "
          f"${limits.max_cap_usd / 1e9:,.1f}B  ({limits.note})")
    print(f"  liquidity : ${limits.min_daily_value_usd / 1e3:,.0f}k traded per day")
    if limits.max_institutional is not None:
        print(f"  ownership : under {limits.max_institutional:.0%} institutional")
    else:
        print(f"  ownership : DISABLED — {limits.ownership_note}")
    if source.endswith("(hand-picked)"):
        print("  ⚠️  This universe was chosen by hand, so anything it produces is "
              "\n      shaped by that choice before any filter runs.")

    result = screen(
        provider, market, symbols, sectors, fx=fx, limits=limits,
        period=args.period,
        progress=lambda i, n: print(f"    ...{i}/{n}", flush=True),
    )

    kept, dropped = result.kept, result.dropped
    if not kept:
        print("\n  Nothing passed. Widen the bands, or check that the data source "
              "is answering at all.")
        return 1

    unknown = result.unknown_ownership
    # The ownership filter is not sector-neutral, and pretending otherwise sends
    # the owner shopping in one industry without noticing. Small banks and REITs
    # structurally carry less institutional ownership than, say, software, so an
    # absolute ceiling selects for them. The skew is reported, and the reading
    # list is capped per sector so a structural bias cannot fill it.
    universe_mix, kept_mix = result.universe_mix, result.kept_mix
    reading = result.reading_list(args.top, args.max_per_sector)

    print(f"\n  {len(kept)} names passed "
          f"(dropped: {dropped['size']} on size, {dropped['liquidity']} on "
          f"liquidity, {dropped['crowded']} already crowded, "
          f"{dropped['no data']} for missing data)")
    if unknown:
        print(f"  {unknown} have no institutional-ownership figure — kept, because "
              f"\n  absent data is not evidence of absence, but treat that filter as "
              f"unproven for them.")

    if universe_mix and kept_mix:
        print("\n  what survived, by sector (share of survivors vs share of universe):")
        total_u = sum(universe_mix.values())
        for sector, n in sorted(kept_mix.items(), key=lambda kv: -kv[1])[:6]:
            share = n / len(kept) * 100
            base = universe_mix.get(sector, 0) / total_u * 100
            flag = "  ← over-represented" if share > base * 2 and share > 15 else ""
            print(f"    {sector[:28]:<30} {share:>5.0f}%  (universe {base:>4.0f}%)"
                  f"{flag}")
        print("    A filter is not sector-neutral. Ownership ceilings favour small "
              "\n    banks and REITs, which carry less institutional money for "
              "structural\n    reasons — that is the filter talking, not a finding "
              "about those sectors.")

    print(f"\n  {'Symbol':<8} {'Sector':<24} {'Cap':>8} {'$/day':>9} "
          f"{'Inst':>6} {'12-1':>8}  trend")
    print("  " + "-" * 74)
    for c in reading:
        held = f"{c.institutional:.0%}" if c.institutional is not None else "  ?"
        print(f"  {c.symbol:<8} {c.sector[:22]:<24} "
              f"${c.cap_usd / 1e9:>6.2f}B {c.daily_value_usd / 1e6:>8.1f}M "
              f"{held:>6} {c.momentum * 100:>7.0f}%  "
              f"{'above 200d' if c.trend_ok else 'below 200d'}")

    if len(reading) < len(kept):
        print(f"\n  Showing {len(reading)} of {len(kept)}, at most "
              f"{args.max_per_sector} per sector.")

    print(f"\n  Read these, do not buy them. The ordering is momentum, which this "
          f"\n  repo has already shown it cannot validate at this basket size — it "
          f"\n  decides reading order and nothing else.")
    print("  What no screen can tell you is whether the business is any good, and "
          "\n  that is the one thing you may genuinely know better than the market. "
          "\n  When you buy, write the thesis first: /thesis add.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
