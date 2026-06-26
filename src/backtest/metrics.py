"""Performance metrics computed from an equity curve / trade list.

Standard, honest definitions: CAGR from first/last equity over elapsed years,
max drawdown as the worst peak-to-trough, Sharpe from daily returns (rf = 0).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

_TRADING_DAYS = 252


@dataclass(frozen=True)
class Metrics:
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    volatility_pct: float
    num_trades: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    final_equity: float
    start_equity: float


def _years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    days = (index[-1] - index[0]).days
    return max(days / 365.25, 1e-9)


def max_drawdown_pct(equity: pd.Series) -> float:
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    return float(drawdown.min() * 100.0)


def sharpe_ratio(equity: pd.Series) -> float:
    rets = equity.pct_change().dropna()
    if rets.empty or rets.std(ddof=0) == 0:
        return 0.0
    return float(rets.mean() / rets.std(ddof=0) * math.sqrt(_TRADING_DAYS))


def annual_vol_pct(equity: pd.Series) -> float:
    rets = equity.pct_change().dropna()
    if rets.empty:
        return 0.0
    return float(rets.std(ddof=0) * math.sqrt(_TRADING_DAYS) * 100.0)


def compute_metrics(equity: pd.Series, trade_returns: list[float]) -> Metrics:
    """equity: indexed by date. trade_returns: per-closed-trade returns (fraction)."""
    start = float(equity.iloc[0])
    final = float(equity.iloc[-1])
    total_ret = (final / start - 1.0) * 100.0
    yrs = _years(equity.index)
    cagr = ((final / start) ** (1.0 / yrs) - 1.0) * 100.0 if start > 0 and yrs > 0 else 0.0

    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]
    win_rate = (len(wins) / len(trade_returns) * 100.0) if trade_returns else 0.0
    avg_win = (sum(wins) / len(wins) * 100.0) if wins else 0.0
    avg_loss = (sum(losses) / len(losses) * 100.0) if losses else 0.0

    return Metrics(
        total_return_pct=round(total_ret, 2),
        cagr_pct=round(cagr, 2),
        max_drawdown_pct=round(max_drawdown_pct(equity), 2),
        sharpe=round(sharpe_ratio(equity), 2),
        volatility_pct=round(annual_vol_pct(equity), 2),
        num_trades=len(trade_returns),
        win_rate_pct=round(win_rate, 1),
        avg_win_pct=round(avg_win, 2),
        avg_loss_pct=round(avg_loss, 2),
        final_equity=round(final, 2),
        start_equity=round(start, 2),
    )
