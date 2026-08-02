"""Run the strategy backtest for US and BIST and print a benchmark comparison.

Usage:
    .venv/bin/python -m scripts.run_backtest            # both markets
    .venv/bin/python -m scripts.run_backtest US         # one market

This is the SPEC §6b validation gate: if the strategy doesn't beat buy-and-hold,
the logic should change before real money is used.
"""
from __future__ import annotations

import logging
import sys

from src.backtest.engine import Backtester, BacktestResult
from src.market.yahoo import YahooProvider
from src.models import Market


def _fmt_metrics(label: str, m) -> str:
    return (
        f"  {label:<22} return {m.total_return_pct:>8.1f}%  "
        f"CAGR {m.cagr_pct:>6.1f}%  maxDD {m.max_drawdown_pct:>7.1f}%  "
        f"Sharpe {m.sharpe:>5.2f}"
    )


def _verdict(r: BacktestResult) -> str:
    """Judge against the equal-weight universe, not the index.

    The index comparison flatters the strategy: the watchlist is a hand-picked
    list of names that are large and successful *today*, so trading them beats a
    broad index partly because of the list. Holding the same names equal-weight
    holds that hindsight constant, so the difference is the timing logic alone.
    """
    reference = r.universe_metrics or r.benchmark_metrics
    label = "equal-wt universe" if r.universe_metrics else "index"
    beat_return = r.metrics.total_return_pct - reference.total_return_pct
    better_dd = r.metrics.max_drawdown_pct - reference.max_drawdown_pct
    flags = [
        ("BEATS" if beat_return > 0 else "TRAILS")
        + f" {label} by {beat_return:+.1f}% total return",
        f"drawdown {'better' if better_dd > 0 else 'worse'} by {better_dd:+.1f}%",
        f"Sharpe {r.metrics.sharpe:.2f} vs {reference.sharpe:.2f}",
    ]
    return " | ".join(flags)


def run_one(market: Market, provider: YahooProvider) -> None:
    print(f"\n{'='*78}\n{market.value} MARKET\n{'='*78}")
    result = Backtester(market, provider).run()
    print(_fmt_metrics("Strategy", result.metrics))
    if result.universe_metrics:
        print(_fmt_metrics("Buy & hold (equal-wt)", result.universe_metrics))
    print(_fmt_metrics("Buy & hold (index)", result.benchmark_metrics))
    m = result.metrics
    print(
        f"\n  trades {m.num_trades}  win-rate {m.win_rate_pct:.0f}%  "
        f"avg win {m.avg_win_pct:+.1f}%  avg loss {m.avg_loss_pct:+.1f}%  "
        f"vol {m.volatility_pct:.0f}%"
    )
    print(f"  period {result.equity.index[0].date()} -> {result.equity.index[-1].date()}")
    print(f"\n  VERDICT: {_verdict(result)}")


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    markets = [Market.US, Market.BIST]
    if len(sys.argv) > 1:
        markets = [Market(sys.argv[1].upper())]
    provider = YahooProvider()
    for mkt in markets:
        try:
            run_one(mkt, provider)
        except Exception as exc:  # noqa: BLE001 - report and continue to next market
            print(f"\n{mkt.value}: backtest failed: {exc}")
    print(
        "\nNOTE: backtest uses technical+macro signals only (no historical "
        "fundamentals/news). Past performance does not guarantee future results."
        "\nNOTE: the watchlist is survivorship-biased — it lists names that are "
        "large and successful today. Judge against the equal-weight universe "
        "column, which holds that bias constant; the index column does not."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
