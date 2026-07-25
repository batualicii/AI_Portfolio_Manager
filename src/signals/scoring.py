"""Scoring — turn indicators / fundamentals / news into normalised sub-scores.

Every sub-score is a float in [-1, 1] (negative = bearish, positive = bullish) with
a list of short human-readable notes explaining the drivers. Those notes are facts
computed here; the LLM later turns them into prose but invents nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.market.types import Fundamentals, NewsItem
from src.signals import indicators as ind
from src.signals.config import SignalConfig


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class SubScore:
    value: float
    notes: list[str] = field(default_factory=list)
    # False when the inputs simply were not there. A 0.0 that means "no data" is
    # not the same as a 0.0 that means "genuinely neutral", and blending the two
    # identically silently dilutes the composite — see SignalEngine.analyze.
    available: bool = True


# ------------------------------- technical -------------------------------

@dataclass
class TechnicalView:
    """Latest indicator readings, surfaced so risk sizing can reuse them."""
    price: float
    atr: float
    rsi: float
    sma50: float | None
    sma200: float | None
    # Highest close over the trailing stop lookback. Lets a held position's stop
    # ratchet up with the trend instead of drifting down with the price.
    recent_high: float = 0.0


def technical_score(df: pd.DataFrame, cfg: SignalConfig) -> tuple[SubScore, TechnicalView]:
    close = df["close"]
    price = float(close.iloc[-1])
    atr_val = float(ind.atr(df, cfg.atr_window).iloc[-1])
    rsi_val = float(ind.rsi(close, cfg.rsi_window).iloc[-1])
    sma50 = ind.sma(close, 50).iloc[-1]
    sma200 = ind.sma(close, 200).iloc[-1]
    _, _, hist = ind.macd(close)
    hist_val = float(hist.iloc[-1])
    roc20 = float(ind.rate_of_change(close, 20).iloc[-1])

    notes: list[str] = []

    # 1) Trend (dominant) — price vs SMA50, SMA50 vs SMA200.
    trend = 0.0
    if pd.notna(sma50):
        above50 = price > sma50
        trend += 0.5 if above50 else -0.5
        notes.append(f"price {'above' if above50 else 'below'} SMA50")
    if pd.notna(sma50) and pd.notna(sma200):
        golden = sma50 > sma200
        trend += 0.5 if golden else -0.5
        notes.append(f"SMA50 {'>' if golden else '<'} SMA200 ({'up' if golden else 'down'}trend)")

    # 2) Momentum — MACD histogram (normalised by ATR) + 20-bar ROC.
    momentum = 0.0
    if atr_val > 0:
        momentum += _clamp(hist_val / atr_val)
    momentum += _clamp(roc20 / 15.0)  # ~15% move over 20 bars saturates
    momentum = _clamp(momentum / 2.0)
    notes.append(f"MACD {'+' if hist_val >= 0 else '-'}, 20d ROC {roc20:+.1f}%")

    # 3) Mean-reversion — RSI extremes (bounce vs exhaustion).
    meanrev = 0.0
    if rsi_val <= 30:
        meanrev = _clamp((30 - rsi_val) / 10.0)      # oversold -> bounce potential
        notes.append(f"RSI {rsi_val:.0f} (oversold)")
    elif rsi_val >= 70:
        meanrev = -_clamp((rsi_val - 70) / 10.0)     # overbought -> extended
        notes.append(f"RSI {rsi_val:.0f} (overbought)")
    else:
        notes.append(f"RSI {rsi_val:.0f}")

    wt = cfg.w_trend + cfg.w_momentum + cfg.w_meanrev
    value = _clamp(
        (cfg.w_trend * trend + cfg.w_momentum * momentum + cfg.w_meanrev * meanrev) / wt
    )
    view = TechnicalView(
        price=price, atr=atr_val, rsi=rsi_val,
        sma50=float(sma50) if pd.notna(sma50) else None,
        sma200=float(sma200) if pd.notna(sma200) else None,
        recent_high=float(close.tail(cfg.hold_stop_lookback).max()),
    )
    return SubScore(value=value, notes=notes), view


# ------------------------------ fundamental ------------------------------

def fundamental_score(f: Fundamentals) -> SubScore:
    parts: list[float] = []
    notes: list[str] = []

    if f.pe_ratio is not None:
        pe = f.pe_ratio
        if pe <= 0:
            parts.append(-0.5); notes.append("negative earnings")
        elif pe < 15:
            parts.append(0.6); notes.append(f"PE {pe:.0f} (cheap)")
        elif pe <= 30:
            parts.append(0.2); notes.append(f"PE {pe:.0f} (fair)")
        elif pe <= 45:
            parts.append(-0.2); notes.append(f"PE {pe:.0f} (rich)")
        else:
            parts.append(-0.6); notes.append(f"PE {pe:.0f} (very rich)")

    if f.revenue_growth is not None:
        rg = f.revenue_growth * 100.0
        parts.append(_clamp(rg / 25.0))  # 25% growth saturates positive
        notes.append(f"rev growth {rg:+.0f}%")

    if f.profit_margin is not None:
        pm = f.profit_margin * 100.0
        parts.append(_clamp(pm / 25.0))
        notes.append(f"margin {pm:+.0f}%")

    if not parts:
        return SubScore(value=0.0, notes=["limited fundamentals"], available=False)
    return SubScore(value=_clamp(sum(parts) / len(parts)), notes=notes)


# ------------------------------- sentiment -------------------------------

_POS = {"beat", "beats", "surge", "soar", "record", "upgrade", "upgraded", "growth",
        "profit", "wins", "win", "rally", "strong", "boost", "raises", "raised",
        "outperform", "buy", "bullish", "jumps", "gains", "approval", "expands"}
_NEG = {"miss", "misses", "plunge", "drop", "downgrade", "downgraded", "loss",
        "lawsuit", "probe", "warning", "warns", "cuts", "cut", "weak", "fraud",
        "recall", "sell", "bearish", "falls", "decline", "slump", "halt", "delay"}


def sentiment_score(news: list[NewsItem]) -> SubScore:
    """Lightweight lexicon sentiment over headlines. Deliberately low-confidence
    (clamped to ±0.5) — it nudges, never dominates. Empty news = neutral."""
    if not news:
        # Permanent state for BIST names — Finnhub has no coverage there.
        return SubScore(value=0.0, notes=["no recent news"], available=False)
    pos = neg = 0
    for item in news:
        words = set(item.headline.lower().replace(",", " ").split())
        pos += len(words & _POS)
        neg += len(words & _NEG)
    total = pos + neg
    if total == 0:
        return SubScore(value=0.0, notes=[f"{len(news)} headlines, neutral tone"])
    raw = (pos - neg) / total
    value = _clamp(raw, -0.5, 0.5)
    return SubScore(
        value=value,
        notes=[f"{len(news)} headlines (pos {pos}/neg {neg})"],
    )
