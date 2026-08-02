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
        # Keep the DatetimeIndex even when empty: callers do index arithmetic
        # (.normalize(), slicing by date) and a bare RangeIndex turns a clean
        # "no data for this market" error into a confusing AttributeError.
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([]),
        )
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


def sustained_below_ma(
    close: pd.Series, ma_window: int = 200, bars: int = 20
) -> bool | None:
    """Has every one of the last `bars` closes sat below the moving average?

    The difference between "the price fell" and "the trend is over". A single
    dip below the average is noise; a sustained one is the story changing. This
    tolerates the deep drawdowns a position must survive to compound while still
    marking a name whose trend has genuinely ended.

    None when there is not enough history to judge, which callers must read as
    "no verdict" rather than as False — an absent answer and a negative one mean
    different things.

    Lives here, in the shared numeric layer, because the backtest's exit rule and
    the live thesis monitor must use the identical definition. This project has
    already shipped one bug where a live formula and its backtest counterpart
    drifted apart (SPEC section 6c); sharing the primitive is how that is
    prevented rather than merely regretted.
    """
    if bars < 1 or len(close) < ma_window + bars:
        return None
    avg = sma(close, ma_window)
    recent = close.iloc[-bars:] < avg.iloc[-bars:]
    return bool(recent.all())


def bars_below_ma(close: pd.Series, ma_window: int = 200) -> int | None:
    """How many closes in a row, right now, sit below the moving average.

    `sustained_below_ma` answers a yes/no a falsifier can fire on. This answers
    "how long has it been like this", which is what a human reading a position
    wants — the difference between a three-day dip and a six-week one. Same
    moving average, so the two can never disagree about what "below" means.

    None when there is not enough history, never 0: "not measured" and "it is
    above the average today" are different statements.
    """
    if len(close) < ma_window:
        return None
    avg = sma(close, ma_window)
    below = (close < avg).iloc[-ma_window:]
    count = 0
    for value in reversed(below.tolist()):
        if not value:
            break
        count += 1
    return count


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
