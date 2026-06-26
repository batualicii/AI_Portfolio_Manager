"""Core domain models — plain data, no I/O.

These dataclasses are the shared language between layers (storage, signals,
digest, bot). Keeping them free of behaviour/I/O keeps the dependencies pointing
one direction: everything depends on models, models depend on nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Market(str, Enum):
    """Which exchange a symbol trades on. Drives data-source and currency handling."""
    US = "US"      # e.g. AAPL  -> USD
    BIST = "BIST"  # e.g. THYAO -> TRY (yfinance ticker THYAO.IS)

    @property
    def currency(self) -> str:
        return "USD" if self is Market.US else "TRY"

    def yf_symbol(self, symbol: str) -> str:
        """Map a plain symbol to its yfinance ticker (BIST needs the .IS suffix)."""
        sym = symbol.upper().strip()
        if self is Market.BIST and not sym.endswith(".IS"):
            return f"{sym}.IS"
        return sym


class Action(str, Enum):
    """A recommendation's directive. Long-only, so no SHORT."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    TRIM = "TRIM"  # partial sell


@dataclass(frozen=True)
class Holding:
    """A position the user currently owns (kept in sync manually from Midas)."""
    symbol: str
    market: Market
    quantity: float
    avg_cost: float       # per-share cost basis, in the market's currency
    note: str = ""

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_cost


@dataclass(frozen=True)
class Recommendation:
    """One buy/sell/hold call for one symbol, with the levels the user should act on.

    Every numeric field here is produced by deterministic signal code — never by the
    LLM. The LLM only fills `rationale` (a human-readable explanation of these numbers).
    """
    symbol: str
    market: Market
    action: Action
    score: float                 # ranking strength, higher = stronger conviction
    price: float                 # reference price the call is based on
    stop_loss: float | None      # mandatory for BUY/HOLD of an open position
    take_profit: float | None
    suggested_weight: float | None  # fraction of portfolio (0-1) for BUY sizing
    rationale: str = ""          # LLM-written explanation (no numbers invented)
    created_at: datetime | None = None
