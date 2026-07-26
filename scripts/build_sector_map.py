"""Map every symbol in the historical universe to a GICS sector.

The point-in-time run left one explanation standing: the edge might be nothing
more than "held semiconductors during a semiconductor boom". Testing that needs a
sector for each name, including the 236 that have since dropped out of the index.

Two sources, in order of trust:

  * Wikipedia's current-membership table carries a GICS Sector column. Free,
    complete for today's members, and already fetched by `build_pit_universe`.
  * Yahoo's `sector` field, for the dropped names Wikipedia no longer lists. Slow
    and rate-limited, so results are cached in the output file and re-runs only
    query what is still missing.

Coverage is written into the file and printed, because a sector cap applied to a
map with large holes tests something other than what it claims to.

Usage:
    python -m scripts.build_sector_map           # fill in whatever is missing
    python -m scripts.build_sector_map --limit 50   # partial pass, resumable
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re

from scripts.build_pit_universe import WIKI, _cells, _fetch

ROOT = pathlib.Path(__file__).resolve().parent.parent
PIT = ROOT / "universes" / "sp500_pit.json"
OUT = ROOT / "universes" / "sectors.json"


def wikipedia_sectors(page: str) -> dict[str, str]:
    """Ticker -> GICS sector for current members."""
    tables = re.findall(r"<table[^>]*wikitable[^>]*>.*?</table>", page, re.S)
    if not tables:
        raise RuntimeError("Wikipedia layout changed: no wikitable found")

    out: dict[str, str] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.S):
        c = _cells(row)
        if len(c) >= 3 and re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", c[0]) and c[2]:
            out[c[0].replace(".", "-")] = c[2]
    return out


def _universe() -> list[str]:
    """Everyone who was a member at any point, plus today's members."""
    if not PIT.exists():
        raise SystemExit(f"missing {PIT.relative_to(ROOT)} — run "
                         f"`python -m scripts.build_pit_universe` first")
    pit = json.loads(PIT.read_text())
    ever: set[str] = set()
    for members in pit["by_year"].values():
        ever.update(members)
    return sorted(ever)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="only query this many missing names from Yahoo (resumable)")
    parser.add_argument("--no-yahoo", action="store_true",
                        help="Wikipedia only; leaves dropped names unmapped")
    args = parser.parse_args()

    symbols = _universe()
    known: dict[str, str] = {}
    if OUT.exists():
        known = dict(json.loads(OUT.read_text()).get("sectors", {}))
        print(f"resuming from {OUT.relative_to(ROOT)} ({len(known)} already mapped)")

    print("fetching Wikipedia sector column...", flush=True)
    from_wiki = wikipedia_sectors(_fetch(WIKI))
    print(f"  {len(from_wiki)} current members carry a GICS sector")
    for sym, sector in from_wiki.items():
        known.setdefault(sym, sector)

    missing = [s for s in symbols if s not in known]
    print(f"\n  universe: {len(symbols)} names ever in the index")
    print(f"  mapped:   {len(symbols) - len(missing)}")
    print(f"  missing:  {len(missing)} (dropped names Wikipedia no longer lists)")

    if missing and not args.no_yahoo:
        from src.market.yahoo import YahooProvider
        from src.models import Market

        todo = missing[: args.limit] if args.limit else missing
        print(f"\n  querying Yahoo for {len(todo)} of them "
              f"(slow; safe to interrupt and re-run)...", flush=True)
        provider = YahooProvider()
        found = 0
        for i, sym in enumerate(todo, 1):
            try:
                sector = provider.get_fundamentals(sym, Market.US).sector
            except Exception:  # noqa: BLE001 — a delisted name failing is the norm here
                sector = None
            if sector:
                known[sym] = sector
                found += 1
            if i % 25 == 0:
                print(f"    {i}/{len(todo)} · {found} resolved", flush=True)
        print(f"    done: {found}/{len(todo)} resolved")

    covered = sum(1 for s in symbols if s in known)
    coverage = covered / len(symbols) if symbols else 0.0

    OUT.write_text(json.dumps({
        "built_at": dt.date.today().isoformat(),
        "sources": [WIKI, "Yahoo Finance `sector` field"],
        "universe_size": len(symbols),
        "covered": covered,
        "coverage_pct": round(coverage * 100, 1),
        "note": ("GICS sector per symbol. Current members come from Wikipedia; "
                 "names that dropped out of the index are resolved via Yahoo and "
                 "may be unavailable if the ticker is fully delisted. Unmapped "
                 "symbols share one bucket in the sector cap, which constrains "
                 "them rather than exempting them."),
        "sectors": dict(sorted(known.items())),
    }, indent=1))

    print(f"\n  coverage: {covered}/{len(symbols)} ({coverage * 100:.0f}%)")
    print(f"  wrote {OUT.relative_to(ROOT)}")

    if coverage < 0.85:
        print("\n  ⚠️  Below 85%: too many names fall into the shared 'Unknown' bucket, "
              "\n  so a sector cap would throttle the unmapped names as a group rather "
              "\n  than spreading the book across industries. Re-run to fill the gaps "
              "\n  before trusting a sector-neutral result.")
    else:
        print("\n  Next: python -m scripts.hold_sector_neutral")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
