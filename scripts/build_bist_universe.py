"""Fetch a BIST universe, so the Turkish screen stops running on a hand-picked list.

The US side has a mechanically defined pond (S&P 600). BIST had nothing, so
`screen_candidates BIST` fell back to the twelve-name watchlist in
`SignalConfig` — the same hand-picked, survivorship-biased list this whole
session has been working to get away from. A screen run over names someone
already liked cannot tell you anything you did not put in.

Turkish Wikipedia's Borsa İstanbul article carries a BIST 100 constituent table
with ticker, company and sector. It is the only free, structured source found;
its limits are real and stated in the output rather than buried:

  * **current membership**, so survivorship-biased like any current index list
  * **maintained by volunteers**, so it lags real index revisions
  * sectors are Turkish-language and do not map onto GICS, so they cannot be
    mixed with the US sector map

Usage:
    python -m scripts.build_bist_universe
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

from scripts.research.build_pit_universe import _cells, _fetch

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "universes" / "bist.json"
WIKI = "https://tr.wikipedia.org/wiki/Borsa_%C4%B0stanbul"


def parse(page: str) -> list[tuple[str, str]]:
    """Return [(ticker, sector), ...] from the constituents table."""
    tables = re.findall(r"<table[^>]*wikitable[^>]*>.*?</table>", page, re.S)
    if not tables:
        raise RuntimeError("Wikipedia layout changed: no wikitable found")

    out: list[tuple[str, str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tables[0], re.S):
        c = _cells(row)
        # Tickers are 4-6 upper-case letters; the header row and any stray rows
        # fail that and are skipped rather than guessed at.
        if len(c) >= 3 and re.fullmatch(r"[A-Z]{4,6}", c[0].strip()):
            sub = c[3].strip() if len(c) > 3 and c[3].strip() else ""
            out.append((c[0].strip(), sub or c[2].strip()))
    return out


def main() -> int:
    print("fetching BIST 100 constituents from Turkish Wikipedia...", flush=True)
    rows = parse(_fetch(WIKI))
    if len(rows) < 60:
        print(f"  only {len(rows)} names parsed — the page layout may have changed; "
              f"not overwriting a good file with a bad one")
        return 1

    symbols = sorted({sym for sym, _ in rows})
    sectors = {sym: sector for sym, sector in rows}

    OUT.write_text(json.dumps({
        "name": "BIST 100",
        "source": WIKI,
        "fetched_at": dt.date.today().isoformat(),
        "note": ("Current membership from a volunteer-maintained article, so it "
                 "lags real index revisions and carries the usual survivorship "
                 "bias (SPEC 6c). Sectors are Turkish-language and do NOT map to "
                 "the GICS labels used for US names — do not merge the two maps."),
        "symbols": symbols,
        "sectors": sectors,
    }, indent=1, ensure_ascii=False))

    by_sector: dict[str, int] = {}
    for sector in sectors.values():
        by_sector[sector] = by_sector.get(sector, 0) + 1

    print(f"  {len(symbols)} names across {len(by_sector)} sectors")
    for sector, count in sorted(by_sector.items(), key=lambda kv: -kv[1])[:6]:
        print(f"    {sector[:34]:<36} {count}")
    print(f"\n  wrote {OUT.relative_to(ROOT)}")
    print("\n  Caveat worth carrying into any result: BIST 100 is the *large* end of "
          "\n  Borsa İstanbul. The capacity advantage this project is chasing lives "
          "\n  further down, and a free list of BIST mid/small caps was not found — "
          "\n  so this is the honest starting point, not the intended pond.")
    print("\n  Next: python -m scripts.screen_candidates BIST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
