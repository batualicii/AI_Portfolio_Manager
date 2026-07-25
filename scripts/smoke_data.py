"""Live smoke test for the market-data layer — run this after any yfinance upgrade.

Yahoo changes its endpoints regularly and yfinance follows; a version bump that
looks harmless can silently turn every quote into None, which the app is designed
to survive quietly. This script makes that failure loud: it exercises quotes,
history, fundamentals, and FX for both markets and prints exactly what came back.

It talks to the abstract MarketDataProvider, not yfinance, so it also verifies the
adapter still satisfies the interface the rest of the app depends on.

Usage: python -m scripts.smoke_data
Exit code 0 = everything the app needs is available; 1 = something is broken.
"""
from __future__ import annotations

import logging

from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals.config import SignalConfig

# One name per market, plus the two regime indices the signal engine relies on.
US_SYMBOL = "AAPL"
BIST_SYMBOL = "THYAO"


def _check(label: str, ok: bool, detail: str) -> bool:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label:<34} {detail}")
    return ok


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    provider = YahooProvider()
    cfg = SignalConfig()
    results: list[bool] = []

    print("\nMarket data smoke test (live Yahoo Finance)\n" + "=" * 60)

    for symbol, market in ((US_SYMBOL, Market.US), (BIST_SYMBOL, Market.BIST)):
        print(f"\n{market.value} — {symbol} (yfinance ticker {market.yf_symbol(symbol)})")

        quote = provider.get_quote(symbol, market)
        results.append(_check(
            "quote", quote is not None,
            f"{quote.price:g} {quote.currency}" if quote else "no price returned",
        ))

        bars = provider.get_history(symbol, market, period=cfg.history_period)
        # The signal engine needs 60+ bars; the backtest warmup needs 200+.
        results.append(_check(
            "history (1y)", len(bars) >= 60,
            f"{len(bars)} bars"
            + (f", {bars[0].day} -> {bars[-1].day}" if bars else ""),
        ))

        fund = provider.get_fundamentals(symbol, market)
        populated = [
            name for name in ("market_cap", "pe_ratio", "revenue_growth", "profit_margin")
            if getattr(fund, name) is not None
        ]
        # Fundamentals are best-effort (BIST coverage is thin), so this is a
        # warning-level signal rather than a hard failure.
        _check("fundamentals (best-effort)", True,
               ", ".join(populated) if populated else "all fields empty")

    print("\nFX + regime indices")
    rate = provider.get_fx_rate("USD", "TRY")
    results.append(_check("USD/TRY", rate is not None,
                          f"{rate:.2f}" if rate else "no rate returned"))

    for market, index_symbol in cfg.index_symbol.items():
        # Index tickers are already full yfinance symbols, so pass Market.US to
        # stop the BIST branch appending a second ".IS" — same call the engine makes.
        idx_bars = provider.get_history(index_symbol, Market.US, period=cfg.history_period)
        results.append(_check(
            f"{market.value} index {index_symbol}",
            len(idx_bars) >= cfg.regime_sma,
            f"{len(idx_bars)} bars (need {cfg.regime_sma} for the SMA regime filter)",
        ))

    passed, total = sum(results), len(results)
    print("\n" + "=" * 60)
    if passed == total:
        print(f"All {total} required checks passed — the data layer is healthy.")
        return 0
    print(f"{total - passed} of {total} required checks FAILED.")
    print("The app degrades quietly on data failures, so fix these before trusting "
          "a digest: check the yfinance version and your network/proxy.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
