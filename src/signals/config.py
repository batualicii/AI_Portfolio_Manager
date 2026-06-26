"""Tunable parameters for the signal engine.

Centralised so the strategy can be adjusted (and later optimised by the backtest in
Stage 4) without hunting through the engine code. Defaults target an AGGRESSIVE,
LONG-ONLY, swing-trading profile (days-to-weeks holding).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.models import Market


@dataclass(frozen=True)
class SignalConfig:
    # --- composite weights (sum need not be 1; score is normalised) ---
    w_technical: float = 0.55   # swing trading is primarily technical
    w_fundamental: float = 0.20
    w_sentiment: float = 0.10
    w_macro: float = 0.15

    # --- action thresholds on composite score in [-1, 1] ---
    buy_threshold: float = 0.35     # candidate must clear this to be a BUY
    sell_threshold: float = -0.30   # held name below this -> SELL
    trim_threshold: float = -0.12   # mild weakness -> TRIM

    # --- risk / sizing (ATR-based) ---
    atr_stop_mult: float = 2.0      # stop = entry - 2*ATR
    atr_target_mult: float = 3.5    # target = entry + 3.5*ATR (reward:risk ~1.75)
    max_position_weight: float = 0.15   # hard cap: no single name > 15% of book
    base_position_weight: float = 0.06  # baseline size for a just-qualifying BUY
    risk_per_trade: float = 0.02    # risk ~2% of equity per trade (sizing input)

    # --- regime filter ---
    # If the market index is below its SMA(regime_sma), risk-off: shrink BUY sizing
    # and raise the BUY bar. Index per market set below.
    regime_sma: int = 200
    risk_off_size_factor: float = 0.5   # halve BUY size in risk-off
    risk_off_buy_penalty: float = 0.10  # add to buy_threshold in risk-off

    # --- data windows ---
    history_period: str = "1y"
    rsi_window: int = 14
    atr_window: int = 14

    # --- candidate universe (watchlist) screened for BUYs, per market ---
    us_universe: tuple[str, ...] = (
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "AMD",
        "TSLA", "NFLX", "JPM", "V", "COST", "LLY", "UNH", "XOM",
    )
    bist_universe: tuple[str, ...] = (
        "THYAO", "ASELS", "KCHOL", "SAHOL", "BIMAS", "EREGL", "SISE",
        "TUPRS", "FROTO", "ASTOR", "PGSUS", "TCELL",
    )

    # Market index tickers for the regime filter (yfinance symbols).
    index_symbol: dict = field(
        default_factory=lambda: {Market.US: "^GSPC", Market.BIST: "XU100.IS"}
    )

    def universe(self, market: Market) -> tuple[str, ...]:
        return self.us_universe if market is Market.US else self.bist_universe
