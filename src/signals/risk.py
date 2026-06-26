"""Risk & sizing — stop-loss, take-profit, and position weight for a trade.

Every BUY gets a mandatory stop (SPEC §1). Levels are ATR-based so they adapt to
each name's volatility. Position size scales with conviction but is hard-capped, and
shrinks in a risk-off regime. All deterministic — no LLM input.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.signals.config import SignalConfig


@dataclass(frozen=True)
class TradeLevels:
    stop_loss: float
    take_profit: float
    suggested_weight: float   # fraction of portfolio (0-1)
    reward_risk: float        # (target-entry)/(entry-stop)


def buy_levels(
    price: float,
    atr: float,
    conviction: float,        # composite score in [buy_threshold, 1]
    cfg: SignalConfig,
    *,
    risk_off: bool,
) -> TradeLevels:
    """Stop/target/size for a new long entry.

    Sizing blends two ideas:
      * conviction scaling — stronger score => larger size (up to the cap), and
      * volatility awareness — wider ATR stops => smaller size for the same risk.
    """
    stop = price - cfg.atr_stop_mult * atr
    target = price + cfg.atr_target_mult * atr
    stop = max(stop, 0.01)

    per_share_risk = max(price - stop, 1e-9)
    reward_risk = (target - price) / per_share_risk

    # Conviction in [0,1] across the actionable band [buy_threshold, 1].
    span = max(1.0 - cfg.buy_threshold, 1e-6)
    conv = max(0.0, min(1.0, (conviction - cfg.buy_threshold) / span))

    # Baseline + conviction bonus toward the cap.
    weight = cfg.base_position_weight + conv * (
        cfg.max_position_weight - cfg.base_position_weight
    )

    # Volatility damping: very volatile names (ATR > 5% of price) get trimmed so
    # the 2-ATR stop doesn't translate into an oversized monetary loss.
    atr_pct = atr / price if price > 0 else 0.0
    if atr_pct > 0.05:
        weight *= 0.05 / atr_pct

    if risk_off:
        weight *= cfg.risk_off_size_factor

    weight = max(0.0, min(weight, cfg.max_position_weight))
    return TradeLevels(
        stop_loss=round(stop, 2),
        take_profit=round(target, 2),
        suggested_weight=round(weight, 4),
        reward_risk=round(reward_risk, 2),
    )


def hold_stop(price: float, atr: float, cfg: SignalConfig) -> float:
    """Protective stop for an existing position we're keeping."""
    return round(max(price - cfg.atr_stop_mult * atr, 0.01), 2)
