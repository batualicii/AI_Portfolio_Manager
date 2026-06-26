"""SignalEngine — orchestrates data -> scores -> decision -> Recommendation.

Flow per symbol:
  1. Pull history / fundamentals / news via the provider interfaces.
  2. Compute technical, fundamental, sentiment sub-scores + a market-regime macro score.
  3. Blend into a composite in [-1, 1].
  4. Map to an Action (long-only) and attach ATR-based stop/target/size.

`scan()` analyses every holding (for SELL/TRIM/HOLD) and every watchlist name not
already held (for BUY), then ranks by urgency and conviction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from src.market.provider import MarketDataProvider, NewsProvider
from src.models import Action, Holding, Market, Recommendation
from src.signals import indicators as ind
from src.signals.config import SignalConfig
from src.signals.risk import buy_levels, hold_stop
from src.signals.scoring import (
    fundamental_score,
    sentiment_score,
    technical_score,
)

log = logging.getLogger(__name__)

_MIN_BARS = 60  # need enough history for SMA50/momentum to be meaningful


@dataclass(frozen=True)
class Regime:
    market: Market
    risk_off: bool
    score: float          # [-1, 1]
    note: str


class SignalEngine:
    def __init__(
        self,
        provider: MarketDataProvider,
        news: NewsProvider,
        cfg: SignalConfig | None = None,
    ) -> None:
        self._provider = provider
        self._news = news
        self._cfg = cfg or SignalConfig()
        self._regime_cache: dict[Market, Regime] = {}

    # ------------------------------ regime ------------------------------

    def regime(self, market: Market) -> Regime:
        if market in self._regime_cache:
            return self._regime_cache[market]
        cfg = self._cfg
        symbol = cfg.index_symbol[market]
        # Index symbols are already full yfinance tickers; pass through US to avoid
        # the BIST .IS suffix being appended to e.g. "XU100.IS".
        bars = self._provider.get_history(
            symbol, Market.US, period=cfg.history_period
        )
        if len(bars) < _MIN_BARS:
            reg = Regime(market, risk_off=False, score=0.0, note="regime unknown")
            self._regime_cache[market] = reg
            return reg
        close = ind.bars_to_frame(bars)["close"]
        sma = ind.sma(close, cfg.regime_sma).iloc[-1]
        price = float(close.iloc[-1])
        roc = float(ind.rate_of_change(close, 20).iloc[-1])
        if pd.notna(sma):
            above = price > sma
            score = (0.6 if above else -0.6) + max(-0.4, min(0.4, roc / 15.0))
            risk_off = not above
            note = (
                f"{market.value} index {'above' if above else 'below'} "
                f"SMA{cfg.regime_sma} (20d {roc:+.1f}%)"
            )
        else:
            score, risk_off, note = max(-0.4, min(0.4, roc / 15.0)), False, "regime: short history"
        reg = Regime(market, risk_off=risk_off, score=max(-1.0, min(1.0, score)), note=note)
        self._regime_cache[market] = reg
        return reg

    # ----------------------------- analysis -----------------------------

    def analyze(
        self, symbol: str, market: Market, holding: Holding | None = None
    ) -> Recommendation | None:
        cfg = self._cfg
        bars = self._provider.get_history(symbol, market, period=cfg.history_period)
        if len(bars) < _MIN_BARS:
            log.info("Skipping %s (%s): only %d bars", symbol, market.value, len(bars))
            return None
        df = ind.bars_to_frame(bars)

        tech, view = technical_score(df, cfg)
        fund = fundamental_score(self._provider.get_fundamentals(symbol, market), cfg)
        sent = sentiment_score(self._news.get_news(symbol, market), cfg)
        reg = self.regime(market)

        denom = cfg.w_technical + cfg.w_fundamental + cfg.w_sentiment + cfg.w_macro
        composite = (
            cfg.w_technical * tech.value
            + cfg.w_fundamental * fund.value
            + cfg.w_sentiment * sent.value
            + cfg.w_macro * reg.score
        ) / denom

        notes = (
            [f"tech {tech.value:+.2f}: " + "; ".join(tech.notes)]
            + [f"fund {fund.value:+.2f}: " + "; ".join(fund.notes)]
            + [f"sentiment {sent.value:+.2f}: " + "; ".join(sent.notes)]
            + [f"macro {reg.score:+.2f}: {reg.note}"]
        )
        rationale = " | ".join(notes)

        if holding is not None:
            return self._decide_held(symbol, market, holding, composite, view, rationale)
        return self._decide_candidate(symbol, market, composite, view, reg.risk_off, rationale)

    def _decide_held(self, symbol, market, holding, composite, view, rationale):
        cfg = self._cfg
        if composite <= cfg.sell_threshold:
            action, stop, target, weight = Action.SELL, None, None, None
        elif composite <= cfg.trim_threshold:
            action = Action.TRIM
            stop, target, weight = hold_stop(view.price, view.atr, cfg), None, None
        else:
            action = Action.HOLD
            stop, target, weight = hold_stop(view.price, view.atr, cfg), None, None
        return self._mk(symbol, market, action, composite, view.price, stop, target, weight, rationale)

    def _decide_candidate(self, symbol, market, composite, view, risk_off, rationale):
        cfg = self._cfg
        threshold = cfg.buy_threshold + (cfg.risk_off_buy_penalty if risk_off else 0.0)
        if composite < threshold:
            return None  # not actionable as a BUY
        levels = buy_levels(view.price, view.atr, composite, cfg, risk_off=risk_off)
        return self._mk(
            symbol, market, Action.BUY, composite, view.price,
            levels.stop_loss, levels.take_profit, levels.suggested_weight,
            rationale + f" | R:R {levels.reward_risk}",
        )

    @staticmethod
    def _mk(symbol, market, action, score, price, stop, target, weight, rationale):
        return Recommendation(
            symbol=symbol, market=market, action=action,
            score=round(float(score), 4), price=round(float(price), 2),
            stop_loss=stop, take_profit=target, suggested_weight=weight,
            rationale=rationale, created_at=datetime.now(timezone.utc),
        )

    # ------------------------------- scan -------------------------------

    def scan(self, holdings: list[Holding]) -> list[Recommendation]:
        """Analyse held names + watchlist candidates, ranked by urgency/conviction."""
        recos: list[Recommendation] = []
        held_keys = {(h.symbol.upper(), h.market) for h in holdings}

        for h in holdings:
            r = self.analyze(h.symbol, h.market, holding=h)
            if r:
                recos.append(r)

        for market in (Market.US, Market.BIST):
            for sym in self._cfg.universe(market):
                if (sym.upper(), market) in held_keys:
                    continue
                r = self.analyze(sym, market)  # candidate -> BUY or None
                if r:
                    recos.append(r)

        recos.sort(key=_rank_key)
        return recos


# Ordering: SELL first (act now), then BUY by conviction, then TRIM, then HOLD.
_ACTION_PRIORITY = {Action.SELL: 0, Action.BUY: 1, Action.TRIM: 2, Action.HOLD: 3}


def _rank_key(r: Recommendation) -> tuple[int, float]:
    priority = _ACTION_PRIORITY.get(r.action, 9)
    # within BUY, higher score first; within SELL, lower (more negative) first.
    secondary = -r.score if r.action == Action.BUY else r.score
    return (priority, secondary)
