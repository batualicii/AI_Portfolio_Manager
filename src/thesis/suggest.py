"""Propose falsifiers from a name's own history, so writing a thesis takes seconds.

Asking someone to hand-type `trend:200/4 | growth:0.20` for every position was
friction this project invented. Worse, it invited round numbers: a 50% drawdown
threshold means something very different for a utility than for a biotech, and
typing the same figure for both is not a decision, it is a habit.

So each suggestion is derived from what the name actually does, and carries the
observation it came from. The owner confirms or overrides — the point is that
they are choosing against a fact rather than against a blank line.

Nothing here is a forecast, and none of it decides anything. Thresholds sit at a
level the name has *not* historically reached, because a falsifier that fires
during ordinary weakness is not a falsifier, it is a stop-loss — and the exit-rule
work in scripts/research established that being stopped out of ordinary weakness
is what destroys a hold strategy.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.market.provider import MarketDataProvider
from src.models import Falsifier, FalsifierKind, Market
from src.signals import indicators as ind


@dataclass(frozen=True)
class Suggestion:
    falsifier: Falsifier
    basis: str          # the observation it was derived from
    spec: str           # ready to paste into /thesis add


def _round_to_5(pct: float) -> float:
    return max(0.05, round(pct * 20) / 20)


def suggest_falsifiers(
    symbol: str, market: Market, provider: MarketDataProvider,
    period: str = "3y",
) -> list[Suggestion]:
    """Falsifiers fitted to this name, each with the number behind it."""
    out: list[Suggestion] = []

    try:
        bars = provider.get_history(symbol, market, period=period)
    except Exception:  # noqa: BLE001
        bars = []
    frame = ind.bars_to_frame(bars)

    if not frame.empty and len(frame) >= 250:
        close = frame["close"]
        worst = float((close / close.cummax() - 1.0).min())
        # Set the line beyond what this name does in a normal bad stretch, so it
        # marks "something changed" rather than "the market had a quarter".
        threshold = _round_to_5(min(0.75, abs(worst) * 1.4))
        out.append(Suggestion(
            Falsifier(FalsifierKind.DRAWDOWN,
                      f"falls {threshold * 100:.0f}% below its high since I bought",
                      threshold=threshold),
            basis=(f"its worst fall in the last {len(close) // 252 or 1}y was "
                   f"{worst * 100:.0f}%, so {threshold * 100:.0f}% is past normal "
                   f"for this name rather than a round number"),
            spec=f"drawdown:{threshold:.2f}",
        ))

        # 200/4 is the setting the exit-rule experiment left standing: it was the
        # only leg with both a defensible sample and a defensible risk profile.
        out.append(Suggestion(
            Falsifier(FalsifierKind.TREND_BREAK,
                      "closes below its 200-day average for 4 straight weeks",
                      lookback=200, persistence=4),
            basis=("four weeks fully below the 200-day average was the exit that "
                   "held up best in scripts/research/hold_exit_rules — it tolerates "
                   "a deep drawdown but still marks a trend that is over"),
            spec="trend:200/4",
        ))

    try:
        fundamentals = provider.get_fundamentals(symbol, market)
    except Exception:  # noqa: BLE001
        return out

    growth = fundamentals.revenue_growth
    if growth is not None and growth > 0.05:
        # Halving is a real deterioration; a small dip is a quarter.
        threshold = _round_to_5(growth * 0.5)
        out.append(Suggestion(
            Falsifier(FalsifierKind.REVENUE_GROWTH,
                      f"revenue growth drops under {threshold * 100:.0f}%",
                      threshold=threshold),
            basis=(f"it is growing at {growth * 100:.0f}% now; half that would be a "
                   f"different company, not a soft quarter"),
            spec=f"growth:{threshold:.2f}",
        ))

    margin = fundamentals.profit_margin
    if margin is not None and margin > 0.03:
        threshold = _round_to_5(margin * 0.6)
        out.append(Suggestion(
            Falsifier(FalsifierKind.PROFIT_MARGIN,
                      f"profit margin drops under {threshold * 100:.0f}%",
                      threshold=threshold),
            basis=(f"margin is {margin * 100:.0f}% today; sustained erosion past "
                   f"{threshold * 100:.0f}% usually means pricing power went"),
            spec=f"margin:{threshold:.2f}",
        ))

    return out


def suggested_command(
    symbol: str, market: Market, price: float, conviction: int,
    summary: str, suggestions: list[Suggestion],
) -> str:
    """A ready-to-edit /thesis add line — the fastest honest path to a record."""
    specs = " | ".join(s.spec for s in suggestions) or "trend:200/4"
    return (f"/thesis add {market.value} {symbol.upper()} {price:g} {conviction} "
            f"| {summary} | {specs}")
