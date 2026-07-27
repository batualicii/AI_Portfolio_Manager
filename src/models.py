"""Core domain models — plain data, no I/O.

These dataclasses are the shared language between layers (storage, signals,
digest, bot). Keeping them free of behaviour/I/O keeps the dependencies pointing
one direction: everything depends on models, models depend on nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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


class FalsifierKind(str, Enum):
    """A condition that would prove a thesis wrong, stated so a machine can check it.

    Written at purchase, when there is no pressure; checked mechanically later,
    when there is. That asymmetry is the whole mechanism — the calm version of
    you sets the terms the anxious version has to argue with.

    Which fields each kind uses:

      TREND_BREAK     lookback = moving-average window in days
                      persistence = consecutive weeks the close must stay below it
      DRAWDOWN        threshold = fraction below the highest close since entry
      REVENUE_GROWTH  threshold = the growth rate it must not fall under
      PROFIT_MARGIN   threshold = the margin it must not fall under
      MANUAL          none; surfaced as a question at review time
    """
    TREND_BREAK = "TREND_BREAK"
    DRAWDOWN = "DRAWDOWN"
    REVENUE_GROWTH = "REVENUE_GROWTH"
    PROFIT_MARGIN = "PROFIT_MARGIN"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class Falsifier:
    """One concrete way the reasoning could turn out to be wrong."""
    kind: FalsifierKind
    text: str                        # the sentence, in the owner's own words
    threshold: float | None = None   # the level that must not be breached
    lookback: int | None = None      # measurement window, in days
    persistence: int | None = None   # consecutive weeks the breach must hold

    @property
    def checkable(self) -> bool:
        """Can code decide this, or does it need a human?

        At least one checkable falsifier is required per thesis (SPEC §6b): a
        position whose only exit condition is "I'll know it when I see it" has
        no exit condition at all.
        """
        return self.kind is not FalsifierKind.MANUAL


@dataclass(frozen=True)
class Thesis:
    """Why a position is owned, and what would change that.

    This replaces `Recommendation` as the centre of the system. A recommendation
    is the machine telling the user what to do; a thesis is the user telling the
    machine what they believe, so it can hold them to it.
    """
    symbol: str
    market: Market
    opened_at: datetime
    entry_price: float
    conviction: int                  # 1-5; drives the position ceiling
    summary: str                     # why this is owned, in the owner's words
    falsifiers: tuple[Falsifier, ...] = ()
    review_every_days: int = 90
    last_reviewed_at: datetime | None = None
    closed_at: datetime | None = None
    closed_reason: str = ""
    id: int | None = None            # assigned by the store

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    @property
    def has_checkable_falsifier(self) -> bool:
        return any(f.checkable for f in self.falsifiers)

    def review_due_on(self) -> datetime:
        since = self.last_reviewed_at or self.opened_at
        return since + timedelta(days=self.review_every_days)

    def review_is_due(self, now: datetime) -> bool:
        return self.is_open and now >= self.review_due_on()
