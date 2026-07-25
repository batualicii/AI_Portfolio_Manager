"""Presentation helpers — render a PortfolioReport as Telegram-friendly text.

Kept apart from valuation logic so the same report can be formatted for the bot
today and the daily digest later without duplicating math.
"""
from __future__ import annotations

from src.bot.markdown import escape_md
from src.models import Market
from src.portfolio.valuation import PortfolioReport


def _sign(v: float) -> str:
    return f"+{v:,.2f}" if v >= 0 else f"{v:,.2f}"


def format_report(report: PortfolioReport) -> str:
    lines: list[str] = ["*Portfolio valuation*"]
    if report.usdtry:
        lines.append(f"USD/TRY {report.usdtry:.2f}")

    for market in (Market.US, Market.BIST):
        rows = [p for p in report.positions if p.holding.market is market]
        if not rows:
            continue
        lines.append(f"\n*{market.value}*")
        for p in rows:
            sym = escape_md(p.holding.symbol)
            if not p.priced:
                lines.append(f"• `{sym}` — no price")
                continue
            pnl_pct = p.unrealized_pnl_pct
            lines.append(
                f"• `{sym}` {p.price:g} {market.currency} | "
                f"val {p.market_value:,.2f} | "
                f"P&L {_sign(p.unrealized_pnl)} ({pnl_pct:+.1f}%)"
            )
        mv, cost = report.subtotal(market)
        lines.append(
            f"  _subtotal {mv:,.0f} {market.currency} "
            f"(P&L {_sign(mv - cost)})_"
        )

    gt_try = report.grand_total_try()
    gt_usd = report.grand_total_usd()
    if gt_try is not None and gt_usd is not None:
        lines.append(f"\n*Total* {gt_try:,.0f} TRY  /  {gt_usd:,.0f} USD")

    if report.unpriced:
        missing = ", ".join(escape_md(p.holding.symbol) for p in report.unpriced)
        lines.append(f"\n⚠️ Could not price: {missing}")

    return "\n".join(lines)
