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

Then a momentum + quality ordering, purely to decide what to read first. The
output is a reading list. Every name still needs the part no screen can do: work
out whether the business is any good, which is where a small investor's real
edge — knowing something specific — actually lives.

BIST thresholds are deliberately more conservative. Spreads are wider, disclosure
is thinner, and the promoted-microcap problem is worse; a screen tuned for US
liquidity would hand back names that cannot be traded out of.

Usage:
    python -m scripts.build_smallcap_universe      # US pond
    python -m scripts.screen_candidates US
    python -m scripts.screen_candidates BIST --universe universes/bist.json
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib

from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals import indicators as ind
from src.signals.config import SignalConfig

ROOT = pathlib.Path(__file__).resolve().parent.parent
SP600 = ROOT / "universes" / "sp600.json"


@dataclasses.dataclass(frozen=True)
class Thresholds:
    """One market's definition of "small enough, but tradeable"."""
    min_cap_usd: float
    max_cap_usd: float
    min_daily_value_usd: float
    max_institutional: float
    note: str


# Caps are stated in USD for both markets and converted, because a fixed TRY
# threshold ages badly under Turkish inflation — the same reason nominal BIST
# returns mislead (SPEC section 6c).
LIMITS = {
    Market.US: Thresholds(
        min_cap_usd=300e6, max_cap_usd=5e9, min_daily_value_usd=1e6,
        max_institutional=0.70,
        note="below ~$5B a large fund cannot build a position that moves its needle",
    ),
    Market.BIST: Thresholds(
        # 750k against a 3B ceiling is a stricter volume-to-size ratio than the
        # US band, which is the intent: spreads are wider here, so the same
        # nominal turnover buys less certainty of getting out. A test pins the
        # ratio, because the first version of these constants said "stricter" in
        # the note and was quietly looser in the numbers.
        min_cap_usd=100e6, max_cap_usd=3e9, min_daily_value_usd=750e3,
        max_institutional=0.70,
        note=("tighter liquidity floor relative to size: BIST spreads are wider and "
              "a position you cannot exit is not a position"),
    ),
}


@dataclasses.dataclass
class Candidate:
    symbol: str
    sector: str
    cap_usd: float
    daily_value_usd: float
    institutional: float | None
    momentum: float
    trend_ok: bool

    @property
    def unknown_ownership(self) -> bool:
        return self.institutional is None


def _universe(market: Market, path: pathlib.Path | None) -> tuple[list[str], dict, str]:
    if path is not None:
        data = json.loads(path.read_text())
        return data["symbols"], data.get("sectors", {}), str(path.name)
    if market is Market.US and SP600.exists():
        data = json.loads(SP600.read_text())
        return data["symbols"], data.get("sectors", {}), "S&P 600 SmallCap"
    cfg = SignalConfig()
    return list(cfg.universe(market)), {}, "built-in watchlist (hand-picked)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market", nargs="?", default="US", choices=["US", "BIST"])
    parser.add_argument("--universe", type=pathlib.Path, default=None)
    parser.add_argument("--top", type=int, default=25, help="how many to read first")
    parser.add_argument("--period", default="2y")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    market = Market(args.market)
    limits = LIMITS[market]
    symbols, sectors, source = _universe(market, args.universe)

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
    print(f"  ownership : under {limits.max_institutional:.0%} institutional")
    if source.endswith("(hand-picked)"):
        print("  ⚠️  This universe was chosen by hand, so anything it produces is "
              "\n      shaped by that choice before any filter runs.")

    kept: list[Candidate] = []
    dropped = {"size": 0, "liquidity": 0, "crowded": 0, "no data": 0}

    for i, symbol in enumerate(symbols, 1):
        if i % 50 == 0:
            print(f"    ...{i}/{len(symbols)}", flush=True)
        try:
            fundamentals = provider.get_fundamentals(symbol, market)
            bars = provider.get_history(symbol, market, period=args.period)
        except Exception:  # noqa: BLE001
            dropped["no data"] += 1
            continue

        if fundamentals.market_cap is None or len(bars) < 220:
            dropped["no data"] += 1
            continue

        cap_usd = fundamentals.market_cap / (fx if market is Market.BIST else 1.0)
        if not (limits.min_cap_usd <= cap_usd <= limits.max_cap_usd):
            dropped["size"] += 1
            continue

        frame = ind.bars_to_frame(bars)
        close = frame["close"]
        recent = frame.iloc[-60:]
        daily_value = float((recent["close"] * recent["volume"]).median())
        daily_value_usd = daily_value / (fx if market is Market.BIST else 1.0)
        if daily_value_usd < limits.min_daily_value_usd:
            dropped["liquidity"] += 1
            continue

        held = fundamentals.held_pct_institutions
        if held is not None and held > limits.max_institutional:
            dropped["crowded"] += 1
            continue

        # Ordering only. Twelve-month return skipping the last month, and whether
        # the name is above its own 200-day average — enough to put the ones
        # already working near the top of the reading list, and nothing more.
        momentum = float(close.iloc[-21] / close.iloc[-252] - 1.0)
        avg = ind.sma(close, 200)
        trend_ok = bool(close.iloc[-1] > avg.iloc[-1]) if avg.notna().any() else False

        kept.append(Candidate(
            symbol, sectors.get(symbol, fundamentals.sector or "Unknown"),
            cap_usd, daily_value_usd, held, momentum, trend_ok,
        ))

    if not kept:
        print("\n  Nothing passed. Widen the bands, or check that the data source "
              "is answering at all.")
        return 1

    kept.sort(key=lambda c: (c.trend_ok, c.momentum), reverse=True)
    unknown = sum(1 for c in kept if c.unknown_ownership)

    print(f"\n  {len(kept)} names passed "
          f"(dropped: {dropped['size']} on size, {dropped['liquidity']} on "
          f"liquidity, {dropped['crowded']} already crowded, "
          f"{dropped['no data']} for missing data)")
    if unknown:
        print(f"  {unknown} have no institutional-ownership figure — kept, because "
              f"\n  absent data is not evidence of absence, but treat that filter as "
              f"unproven for them.")

    print(f"\n  {'Symbol':<8} {'Sector':<24} {'Cap':>8} {'$/day':>9} "
          f"{'Inst':>6} {'12-1':>8}  trend")
    print("  " + "-" * 74)
    for c in kept[:args.top]:
        held = f"{c.institutional:.0%}" if c.institutional is not None else "  ?"
        print(f"  {c.symbol:<8} {c.sector[:22]:<24} "
              f"${c.cap_usd / 1e9:>6.2f}B {c.daily_value_usd / 1e6:>8.1f}M "
              f"{held:>6} {c.momentum * 100:>7.0f}%  "
              f"{'above 200d' if c.trend_ok else 'below 200d'}")

    print(f"\n  Read these, do not buy them. The ordering is momentum, which this "
          f"\n  repo has already shown it cannot validate at this basket size — it "
          f"\n  decides reading order and nothing else.")
    print("  What no screen can tell you is whether the business is any good, and "
          "\n  that is the one thing you may genuinely know better than the market. "
          "\n  When you buy, write the thesis first: /thesis add.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
