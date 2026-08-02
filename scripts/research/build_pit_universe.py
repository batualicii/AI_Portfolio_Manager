"""Reconstruct point-in-time S&P 500 membership — the test everything else needs.

Every result so far has been measured on *today's* index membership. That deletes
the companies which fell out of the index over the test window, which is exactly
where a momentum strategy's worst outcomes live: buying a name near its top and
riding it down until it is removed. A strategy tested on survivors only has had
its failure mode edited out of the data.

Wikipedia's "Selected changes to the list of S&P 500 components" table lists every
addition and removal with an effective date. Starting from current membership and
walking those changes *backwards* reconstructs who was actually in the index on
any past date.

That fixes half the problem. The other half is prices: a name removed after an
acquisition or a collapse may no longer be quotable, and if the dropped names have
no data then the bias survives the reconstruction. This script therefore reports
coverage rather than assuming it — a reconstruction that cannot be priced is worth
knowing about before it silently becomes the basis of a result.

Usage:
    python -m scripts.research.build_pit_universe            # build and report
    python -m scripts.research.build_pit_universe --check    # also probe price availability
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "universes" / "sp500_pit.json"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _cells(row: str) -> list[str]:
    return [
        html.unescape(re.sub("<[^>]+>", "", c)).strip()
        for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
    ]


def parse(page: str) -> tuple[set[str], list[tuple[dt.date, str, str]]]:
    """Return (current members, [(date, added_ticker, removed_ticker), ...])."""
    tables = re.findall(r"<table[^>]*wikitable[^>]*>.*?</table>", page, re.S)
    if len(tables) < 2:
        raise RuntimeError("Wikipedia layout changed: expected two wikitables")

    current = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.S):
        c = _cells(row)
        if c and re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", c[0]):
            current.add(c[0].replace(".", "-"))

    changes: list[tuple[dt.date, str, str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[1], re.S):
        c = _cells(row)
        if len(c) < 5 or not re.match(r"[A-Z][a-z]+ \d+, \d{4}", c[0]):
            continue
        try:
            when = dt.datetime.strptime(c[0], "%B %d, %Y").date()
        except ValueError:
            continue
        added = c[1].replace(".", "-") if re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", c[1]) else ""
        removed = c[3].replace(".", "-") if re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", c[3]) else ""
        if added or removed:
            changes.append((when, added, removed))
    return current, changes


def membership_by_year(
    current: set[str], changes: list[tuple[dt.date, str, str]], years: range
) -> dict[str, list[str]]:
    """Walk changes backwards from today to recover membership at each Jan 1."""
    changes = sorted(changes, key=lambda c: c[0], reverse=True)
    members = set(current)
    out: dict[str, list[str]] = {}
    cursor = dt.date.today()

    for year in sorted(years, reverse=True):
        target = dt.date(year, 1, 1)
        for when, added, removed in changes:
            if target <= when < cursor:
                # Undo the change: an addition had not happened yet, and a name
                # removed on that date was still a member before it.
                if added:
                    members.discard(added)
                if removed:
                    members.add(removed)
        cursor = target
        out[str(year)] = sorted(members)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-year", type=int, default=2015)
    parser.add_argument("--to-year", type=int, default=dt.date.today().year)
    parser.add_argument("--check", action="store_true",
                        help="probe Yahoo for the dropped names (slow, rate-limited)")
    args = parser.parse_args()

    print("fetching Wikipedia change history...", flush=True)
    current, changes = parse(_fetch(WIKI))
    print(f"  current members: {len(current)}")
    print(f"  recorded changes: {len(changes)} "
          f"({min(c[0] for c in changes)} .. {max(c[0] for c in changes)})")

    years = range(args.from_year, args.to_year + 1)
    by_year = membership_by_year(current, changes, years)

    ever = set()
    for members in by_year.values():
        ever.update(members)
    dropped = sorted(ever - current)

    print(f"\n  reconstructed {len(by_year)} yearly snapshots")
    for year in sorted(by_year):
        print(f"    {year}: {len(by_year[year])} members")
    print(f"\n  names that appear historically but are NOT in today's index: "
          f"{len(dropped)}")
    print(f"    these are precisely what survivorship bias hides: "
          f"{', '.join(dropped[:12])}{' ...' if len(dropped) > 12 else ''}")

    payload = {
        "name": "S&P 500 (point-in-time)",
        "source": WIKI,
        "built_at": dt.date.today().isoformat(),
        "note": ("Membership reconstructed by walking Wikipedia's change table "
                 "backwards from current membership. Accuracy depends on that "
                 "table being complete; it is described as 'selected changes', so "
                 "treat early years as approximate."),
        "current_count": len(current),
        "dropped_names": dropped,
        "by_year": by_year,
    }
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"\n  wrote {OUT.relative_to(ROOT)}")

    if args.check:
        print("\n  probing price availability for dropped names "
              "(this decides whether the fix actually works)...", flush=True)
        from src.market.yahoo import YahooProvider
        from src.models import Market

        provider = YahooProvider()
        have = miss = 0
        missing_examples: list[str] = []
        for sym in dropped[:40]:
            bars = provider.get_history(sym, Market.US, period="5y")
            if len(bars) > 100:
                have += 1
            else:
                miss += 1
                if len(missing_examples) < 10:
                    missing_examples.append(sym)
        total = have + miss
        if total:
            print(f"    {have}/{total} sampled dropped names still have price history "
                  f"({have / total * 100:.0f}%)")
            if missing_examples:
                print(f"    unavailable: {', '.join(missing_examples)}")
            if have / total < 0.6:
                print("\n    ⚠️  Most dropped names cannot be priced, so a point-in-time "
                      "\n    backtest would still quietly exclude them. The bias would be "
                      "\n    reduced, not removed — say so in any result built on this.")

    print("\n  Next: run the selection strategy against these yearly snapshots and "
          "\n  compare to the same run on today's membership. The gap between them "
          "\n  IS the survivorship bias, measured rather than argued about.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
