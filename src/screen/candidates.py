"""The screen itself: filters, thresholds, and the sweep that applies them.

This lived inside `scripts/screen_candidates.py` while it was only ever run from
a terminal. The bot now serves the same list over Telegram, and two copies of a
filter is exactly the failure this repo already hit once — the live scoring
formula drifting from the backtested one (SPEC section 6, defect 16). So the
rules live here, imported by both, and there is only one of them.

Three filters, each a real constraint rather than a preference:

    size        small enough that large funds structurally cannot be here
    liquidity   large enough that *you* can get in and out
    ownership   not already crowded with institutions

The ordering that follows is momentum, and it is **not** a quality ranking. This
repo measured that a price-based ranking cannot be validated at this portfolio
size (SPEC section 6c); the ordering decides what gets read first and nothing
else. Callers are expected to say so in their output, and both of them do.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import pathlib

from src.market.provider import MarketDataProvider
from src.models import Market
from src.signals import indicators as ind

log = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SP600 = ROOT / "universes" / "sp600.json"
BIST = ROOT / "universes" / "bist.json"


@dataclasses.dataclass(frozen=True)
class Thresholds:
    """One market's definition of "small enough, but tradeable"."""
    min_cap_usd: float
    max_cap_usd: float
    min_daily_value_usd: float
    max_institutional: float | None   # None = the data does not support this filter
    note: str
    ownership_note: str = ""


LIMITS: dict[Market, Thresholds] = {
    Market.US: Thresholds(
        min_cap_usd=200e6, max_cap_usd=4e9, min_daily_value_usd=800e3,
        max_institutional=0.85,
        note="below the size a multi-billion fund can take a real position in",
    ),
    Market.BIST: Thresholds(
        # 750k against a 3B ceiling is a stricter volume-to-size ratio than the
        # US band, which is the intent: spreads are wider here, so the same
        # nominal turnover buys less certainty of getting out. A test pins the
        # ratio, because the first version of these constants said "stricter" in
        # the note and was quietly looser in the numbers.
        min_cap_usd=100e6, max_cap_usd=3e9, min_daily_value_usd=750e3,
        # Disabled, not merely unpopulated. Yahoo does report a number for BIST
        # tickers, but it counts US 13F filers only — which is near zero for
        # every Turkish company, so a threshold on it never binds and the run
        # would show three filters while two were doing the work. A filter that
        # silently never fires is worse than an absent one, because the output
        # looks like it passed a test it never took.
        max_institutional=None,
        note=("tighter liquidity floor relative to size: BIST spreads are wider and "
              "a position you cannot exit is not a position"),
        ownership_note=("Yahoo's figure for BIST counts US institutional filers only "
                        "— it reads ~4% for household names, which is real but "
                        "measures something else. Filter disabled rather than run "
                        "on a number that means the wrong thing."),
    ),
}


def load_universe(
    market: Market, path: pathlib.Path | None = None
) -> tuple[list[str], dict, str]:
    """The pond, and an honest name for where it came from.

    The built-in watchlist is the last resort and is labelled "hand-picked",
    because a universe someone chose by hand has already shaped the result
    before a single filter runs.
    """
    if path is not None:
        data = json.loads(path.read_text())
        return data["symbols"], data.get("sectors", {}), str(path.name)
    if market is Market.US and SP600.exists():
        data = json.loads(SP600.read_text())
        return data["symbols"], data.get("sectors", {}), "S&P 600 SmallCap"
    if market is Market.BIST and BIST.exists():
        data = json.loads(BIST.read_text())
        return data["symbols"], data.get("sectors", {}), "BIST 100"

    from src.signals.config import SignalConfig
    return list(SignalConfig().universe(market)), {}, "built-in watchlist (hand-picked)"


@dataclasses.dataclass(frozen=True)
class Candidate:
    """One name that survived the filters, with what was read while filtering.

    The fundamentals are carried rather than refetched. A pool of twenty names
    would otherwise cost twenty more calls to display, and the figures shown
    would be from a different moment than the figures filtered on.
    """
    symbol: str
    sector: str
    cap_usd: float
    daily_value_usd: float
    institutional: float | None
    momentum: float
    trend_ok: bool
    vs_200d: float | None = None      # fraction above/below the 200-day average
    price: float | None = None
    revenue_growth: float | None = None
    profit_margin: float | None = None
    pe_ratio: float | None = None
    beta: float | None = None
    worst_drawdown: float | None = None   # deepest peak-to-trough, as a fraction

    @property
    def unknown_ownership(self) -> bool:
        return self.institutional is None


@dataclasses.dataclass
class ScreenResult:
    market: Market
    kept: list[Candidate]
    dropped: dict[str, int]
    universe_mix: dict[str, int]
    universe_size: int

    @property
    def kept_mix(self) -> dict[str, int]:
        mix: dict[str, int] = {}
        for c in self.kept:
            mix[c.sector] = mix.get(c.sector, 0) + 1
        return mix

    @property
    def unknown_ownership(self) -> int:
        return sum(1 for c in self.kept if c.unknown_ownership)

    def reading_list(self, top: int = 20, max_per_sector: int = 3) -> list[Candidate]:
        """The first `top` names, capped per sector.

        The cap is not cosmetic. The ownership filter is not sector-neutral —
        small banks and REITs carry less institutional money for structural
        reasons — so without it a list of twenty is a list of twenty banks, and
        the reader mistakes the filter for a finding.
        """
        out: list[Candidate] = []
        per_sector: dict[str, int] = {}
        for c in self.kept:
            if per_sector.get(c.sector, 0) >= max_per_sector:
                continue
            out.append(c)
            per_sector[c.sector] = per_sector.get(c.sector, 0) + 1
            if len(out) >= top:
                break
        return out


def screen(
    provider: MarketDataProvider,
    market: Market,
    symbols: list[str],
    sectors: dict[str, str],
    fx: float = 1.0,
    limits: Thresholds | None = None,
    period: str = "2y",
    progress=None,
) -> ScreenResult:
    """Sweep a universe once and keep what passes. Never raises on one bad name.

    `fx` converts the market's own currency to USD, because a fixed TRY
    threshold ages badly under Turkish inflation — the same reason nominal BIST
    levels cannot be compared across years.
    """
    limits = limits or LIMITS[market]
    kept: list[Candidate] = []
    dropped = {"size": 0, "liquidity": 0, "crowded": 0, "no data": 0}
    rate = fx if market is Market.BIST else 1.0

    for i, symbol in enumerate(symbols, 1):
        if progress is not None and i % 50 == 0:
            progress(i, len(symbols))
        try:
            f = provider.get_fundamentals(symbol, market)
            bars = provider.get_history(symbol, market, period=period)
        except Exception:  # noqa: BLE001 — one unreachable name is not a failure
            dropped["no data"] += 1
            continue

        if f.market_cap is None or len(bars) < 220:
            dropped["no data"] += 1
            continue

        cap_usd = f.market_cap / rate
        if not (limits.min_cap_usd <= cap_usd <= limits.max_cap_usd):
            dropped["size"] += 1
            continue

        frame = ind.bars_to_frame(bars)
        close = frame["close"]
        recent = frame.iloc[-60:]
        daily_value_usd = float((recent["close"] * recent["volume"]).median()) / rate
        if daily_value_usd < limits.min_daily_value_usd:
            dropped["liquidity"] += 1
            continue

        held = f.held_pct_institutions
        if (limits.max_institutional is not None and held is not None
                and held > limits.max_institutional):
            dropped["crowded"] += 1
            continue

        # Ordering only. Twelve-month return skipping the last month, and whether
        # the name is above its own 200-day average.
        momentum = float(close.iloc[-21] / close.iloc[-252] - 1.0)
        avg = ind.sma(close, 200)
        has_avg = bool(avg.notna().any())
        trend_ok = bool(close.iloc[-1] > avg.iloc[-1]) if has_avg else False
        vs_200d = float(close.iloc[-1] / avg.iloc[-1] - 1.0) if has_avg else None
        worst = float((close / close.cummax() - 1.0).min())

        kept.append(Candidate(
            symbol=symbol,
            sector=sectors.get(symbol) or f.sector or "Unknown",
            cap_usd=cap_usd,
            daily_value_usd=daily_value_usd,
            institutional=held,
            momentum=momentum,
            trend_ok=trend_ok,
            vs_200d=vs_200d,
            price=float(close.iloc[-1]),
            revenue_growth=f.revenue_growth,
            profit_margin=f.profit_margin,
            pe_ratio=f.pe_ratio,
            beta=f.beta,
            worst_drawdown=worst,
        ))

    kept.sort(key=lambda c: (c.trend_ok, c.momentum), reverse=True)

    universe_mix: dict[str, int] = {}
    for sector in sectors.values():
        universe_mix[sector] = universe_mix.get(sector, 0) + 1

    return ScreenResult(market, kept, dropped, universe_mix, len(symbols))
