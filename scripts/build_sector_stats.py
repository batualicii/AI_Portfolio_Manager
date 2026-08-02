"""Measure what normal looks like, per sector, so a number can be read.

"P/E 24" means nothing on its own. "P/E 24, the median in its sector is 18"
means something a reader can act on. The difference is a reference point, and
the honest reference point is what the company's own peers actually do — not a
rule of thumb about what is expensive.

This walks a universe once, records growth, margin, P/E and beta per sector, and
writes the medians and full distributions. Slow (one fundamentals call per name)
and worth re-running a few times a year, not daily.

Coverage is reported rather than assumed: a sector with four usable names has a
median that should not be quoted, and the output says which ones those are.

Usage:
    python -m scripts.build_sector_stats            # S&P 600
    python -m scripts.build_sector_stats --universe universes/bist.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import statistics

from src.market.yahoo import YahooProvider
from src.models import Market

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "universes" / "sp600.json"


def out_path(market: str) -> pathlib.Path:
    """One file per market. A single shared file meant the second run erased
    the first, and silently handed BIST names US medians as though they were
    peers — different economy, different cost of capital, different normal."""
    return ROOT / "universes" / f"sector_stats_{market}.json"

FIELDS = ("revenue_growth", "profit_margin", "pe_ratio", "beta")
MIN_SAMPLE = 8   # below this a median is a rumour, not a statistic


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=pathlib.Path, default=DEFAULT)
    parser.add_argument("--market", default="US", choices=["US", "BIST"])
    args = parser.parse_args()

    if not args.universe.exists():
        print(f"missing {args.universe} — build a universe first")
        return 1

    logging.basicConfig(level=logging.WARNING)
    data = json.loads(args.universe.read_text())
    symbols, sectors = data["symbols"], data.get("sectors", {})
    market = Market(args.market)
    provider = YahooProvider()

    print(f"reading fundamentals for {len(symbols)} names "
          f"(slow — one call each)...", flush=True)

    raw: dict[str, dict[str, list[float]]] = {}
    # Every usable value, regardless of sector. On BIST this is the only
    # reference that exists: ~100 names over ~34 sectors leaves no sector with a
    # sample, but the market as a whole has one. It mixes a bank with an airline
    # and is therefore a level, not a peer comparison — labelled as such
    # everywhere it is used, and never quietly presented as "sector".
    overall: dict[str, list[float]] = {k: [] for k in FIELDS}
    seen = 0
    for i, symbol in enumerate(symbols, 1):
        if i % 50 == 0:
            print(f"  ...{i}/{len(symbols)}", flush=True)
        try:
            f = provider.get_fundamentals(symbol, market)
        except Exception:  # noqa: BLE001
            continue
        sector = sectors.get(symbol) or f.sector
        bucket = raw.setdefault(sector, {k: [] for k in FIELDS}) if sector else None
        got = False
        for field in FIELDS:
            value = getattr(f, field, None)
            # P/E is meaningless when negative and distorts a median badly.
            if value is None or (field == "pe_ratio" and value <= 0):
                continue
            if bucket is not None:
                bucket[field].append(float(value))
            overall[field].append(float(value))
            got = True
        seen += got

    def summarise(values: list[float]) -> dict | None:
        if len(values) < MIN_SAMPLE:
            return None
        trimmed = sorted(values)[1:-1] or values   # drop the two extremes
        return {
            "median": statistics.median(trimmed),
            # What the median actually rests on, after trimming — two fewer than
            # the sample the min_sample gate checked. Reporting the gate's number
            # would overstate the evidence by exactly the two values discarded.
            "n": len(trimmed),
            "n_raw": len(values),
            "values": [round(v, 4) for v in trimmed],
        }

    out: dict[str, dict] = {}
    omitted: dict[str, dict[str, int]] = {}
    for sector, fields in raw.items():
        entry: dict[str, dict] = {}
        short: dict[str, int] = {}
        for field, values in fields.items():
            if len(values) < MIN_SAMPLE:
                # Recorded, not discarded: /brief can then say "Utilities had 5
                # usable names" instead of leaving a silent blank, which reads
                # as "nothing to report".
                short[field] = len(values)
                continue
            entry[field] = summarise(values)
        if entry:
            out[sector] = entry
        else:
            omitted[sector] = short

    market_wide = {f: summarise(v) for f, v in overall.items()}
    market_wide = {f: v for f, v in market_wide.items() if v}

    out_file = out_path(market.value)
    out_file.write_text(json.dumps({
        "built_at": dt.date.today().isoformat(),
        "universe": str(args.universe.name),
        "market": market.value,
        "min_sample": MIN_SAMPLE,
        "note": ("Medians of a company's own sector peers, so a metric can be "
                 "read against something real instead of a rule of thumb. "
                 "Sectors with fewer than min_sample usable values are omitted "
                 "rather than reported thin — a median of four is a rumour. "
                 "Valid within a sector only: a bank's margin and a retailer's "
                 "margin do not measure the same thing, so these medians rank "
                 "companies among peers and never one sector against another. "
                 "`overall` is the whole market in one bucket, used where a "
                 "sector has no sample — BIST, almost entirely. It mixes "
                 "industries on purpose and is therefore a level to read "
                 "against, not a peer comparison; consumers must label it as "
                 "such. `n` counts the values behind each median after the two "
                 "extremes are trimmed, so it is two below the `n_raw` the "
                 "min_sample gate saw — the smaller number is the honest one."),
        "sectors": out,
        "omitted": omitted,
        "overall": market_wide,
    }, indent=1, ensure_ascii=False))

    print(f"\n  {seen} names had usable fundamentals")
    print(f"  {len(out)} sectors have enough data to quote a median")
    thin = [s for s, e in out.items()
            if any(f["n"] < 25 for f in e.values())]
    if thin:
        print(f"  {len(thin)} have medians under n=25 — /brief prints the "
              f"sample size so they read as the weaker evidence they are: "
              f"{', '.join(sorted(thin)[:6])}")
    if omitted:
        print(f"  {len(omitted)} omitted for thin coverage: "
              f"{', '.join(sorted(omitted)[:6])}")
    if market_wide:
        n = max(v["n"] for v in market_wide.values())
        print(f"  market-wide fallback built from {n} names — used only where a "
              f"sector has no sample, and always labelled as a cross-sector level")
    print(f"\n  wrote {out_file.relative_to(ROOT)}")
    print("  /brief will now place each number against its sector.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
