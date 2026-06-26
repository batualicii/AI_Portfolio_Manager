"""Live portfolio valuation: current price, market value, and unrealized P&L.

Combines holdings (from storage) with live quotes (from a MarketDataProvider).
US positions are valued in USD, BIST in TRY; a grand total is also expressed in
both currencies using the live USD/TRY rate so the user sees one bottom line.

Pure computation over the provider interface — no yfinance import here.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.market.provider import MarketDataProvider
from src.models import Holding, Market


@dataclass(frozen=True)
class PositionValue:
    holding: Holding
    price: float | None          # current price, None if unavailable
    day_change_pct: float | None

    @property
    def priced(self) -> bool:
        return self.price is not None

    @property
    def market_value(self) -> float | None:
        if self.price is None:
            return None
        return self.price * self.holding.quantity

    @property
    def unrealized_pnl(self) -> float | None:
        mv = self.market_value
        if mv is None:
            return None
        return mv - self.holding.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float | None:
        if self.price is None or self.holding.avg_cost == 0:
            return None
        return (self.price - self.holding.avg_cost) / self.holding.avg_cost * 100.0


@dataclass(frozen=True)
class PortfolioReport:
    positions: list[PositionValue]
    usdtry: float | None

    # Per-currency subtotals (native).
    def subtotal(self, market: Market) -> tuple[float, float]:
        """(market_value, cost_basis) summed for one market, priced positions only."""
        mv = cost = 0.0
        for p in self.positions:
            if p.holding.market is market and p.market_value is not None:
                mv += p.market_value
                cost += p.holding.cost_basis
        return mv, cost

    @property
    def unpriced(self) -> list[PositionValue]:
        return [p for p in self.positions if not p.priced]

    def grand_total_try(self) -> float | None:
        """Total market value in TRY (US converted via usdtry). None if no FX rate."""
        if self.usdtry is None:
            return None
        us_mv, _ = self.subtotal(Market.US)
        bist_mv, _ = self.subtotal(Market.BIST)
        return us_mv * self.usdtry + bist_mv

    def grand_total_usd(self) -> float | None:
        if self.usdtry in (None, 0):
            return None
        us_mv, _ = self.subtotal(Market.US)
        bist_mv, _ = self.subtotal(Market.BIST)
        return us_mv + bist_mv / self.usdtry


class ValuationService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def value(self, holdings: list[Holding]) -> PortfolioReport:
        positions: list[PositionValue] = []
        for h in holdings:
            quote = self._provider.get_quote(h.symbol, h.market)
            positions.append(
                PositionValue(
                    holding=h,
                    price=quote.price if quote else None,
                    day_change_pct=quote.day_change_pct if quote else None,
                )
            )
        usdtry = self._provider.get_fx_rate("USD", "TRY")
        return PortfolioReport(positions=positions, usdtry=usdtry)
