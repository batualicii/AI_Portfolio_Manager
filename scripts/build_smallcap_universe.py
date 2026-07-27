"""Fetch a small-cap universe — the pond large funds cannot fish in.

The S&P 500 work in this repo screened the most analysed market on earth with
free data, which is why it found nothing (SPEC section 6c). The one structural
advantage a small account has is capacity: a fund running billions cannot take a
meaningful position in a $500M company without owning half of it, so those names
carry far less institutional attention. That is where an individual is not
competing with people who have better data and more of it.

The S&P 600 SmallCap is a clean, mechanically defined, freely listed proxy for
that band. It is not the whole small-cap market — it excludes the unprofitable
and the very smallest, which is a **feature** here: those are also where the
worst data quality and the loudest promotion live.

This is current membership, so it carries the same survivorship bias as any
current index list (SPEC section 6c). It is a research queue, not a backtest
universe, and the difference matters: a human reads every name before anything
is bought, which is precisely the filter a backtest does not have.

Usage:
    python -m scripts.build_smallcap_universe
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

from scripts.research.build_pit_universe import _cells, _fetch

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "universes" / "sp600.json"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies"


def parse(page: str) -> list[tuple[str, str]]:
    """Return [(ticker, sector), ...] from the constituents table."""
    tables = re.findall(r"<table[^>]*wikitable[^>]*>.*?</table>", page, re.S)
    if not tables:
        raise RuntimeError("Wikipedia layout changed: no wikitable found")

    out: list[tuple[str, str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.S):
        c = _cells(row)
        if len(c) >= 3 and re.fullmatch(r"[A-Z][A-Z.\-]{0,6}", c[0]):
            out.append((c[0].replace(".", "-"), c[2]))
    return out


def main() -> int:
    print("fetching S&P 600 constituents...", flush=True)
    rows = parse(_fetch(WIKI))
    if len(rows) < 400:
        print(f"  only {len(rows)} names parsed — the page layout may have changed; "
              f"not overwriting a good file with a bad one")
        return 1

    symbols = sorted({sym for sym, _ in rows})
    sectors = {sym: sector for sym, sector in rows}

    OUT.write_text(json.dumps({
        "name": "S&P 600 SmallCap",
        "source": WIKI,
        "fetched_at": dt.date.today().isoformat(),
        "note": ("Current membership, so survivorship-biased in the same way any "
                 "current index list is (SPEC 6c). Intended as a research queue "
                 "for names institutions cannot meaningfully own, not as a "
                 "backtest universe."),
        "symbols": symbols,
        "sectors": sectors,
    }, indent=1))

    by_sector: dict[str, int] = {}
    for sector in sectors.values():
        by_sector[sector] = by_sector.get(sector, 0) + 1

    print(f"  {len(symbols)} names across {len(by_sector)} sectors")
    for sector, count in sorted(by_sector.items(), key=lambda kv: -kv[1])[:5]:
        print(f"    {sector[:28]:<30} {count}")
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    print("\n  Next: python -m scripts.screen_candidates US")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
