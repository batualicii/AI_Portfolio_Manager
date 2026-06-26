"""Technical indicators — pure functions over price series.

Implemented directly on pandas/numpy (no pandas-ta, which is broken on pandas 3 /
numpy 2). Each function takes a pandas Series/DataFrame and returns a Series aligned
to the input index, so callers can read `.iloc[-1]` for the latest value.

All formulas use standard conventions (Wilder's smoothing for RSI/ATR).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.market.types import Bar


def bars_to_frame(bars: list[Bar]) -> pd.DataFrame:
    """Convert provider Bars into an OHLCV DataFrame indexed by date."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.to_datetime([b.day for b in bars]),
    )
    return df


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI in [0, 100]."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EMA with alpha = 1/window.
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # If avg_loss is 0 (pure uptrend), RSI = 100.
    out = out.where(avg_loss != 0.0, 100.0)
    return out


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def bollinger(
    close: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (middle, upper, lower) bands."""
    mid = sma(close, window)
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    return mid, mid + num_std * std, mid - num_std * std


def rate_of_change(close: pd.Series, window: int) -> pd.Series:
    """Percent change over `window` bars, in percent."""
    return close.pct_change(periods=window) * 100.0


def pct_from_high(close: pd.Series, window: int) -> pd.Series:
    """Percent below the rolling `window`-bar high (0 = at the high, negative below)."""
    roll_high = close.rolling(window=window, min_periods=1).max()
    return (close - roll_high) / roll_high * 100.0
