"""Point-in-time backtester for the signal strategy.

No lookahead: on each simulated day, scores use only data up to and including that
day. Entries happen on weekly rebalances; stops/targets are checked every day.
A commission+slippage cost is charged on every fill.

Scope: technical + macro sub-scores only (no historical fundamentals/news exist for
free), renormalised over their two weights. This validates the strategy's backbone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from src.backtest.metrics import Metrics, compute_metrics
from src.market.provider import MarketDataProvider
from src.models import Market
from src.signals import indicators as ind
from src.signals.config import SignalConfig
from src.signals.risk import buy_levels
from src.signals.scoring import technical_score

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestConfig:
    start_equity: float = 100_000.0
    period: str = "5y"          # yfinance period to pull
    warmup_bars: int = 200      # need SMA200 before trading
    rebalance_every: int = 5    # trading days between rebalances (~weekly = swing)
    commission_pct: float = 0.001  # 0.1% per fill (commission + slippage)
    max_invested: float = 1.0   # cap total deployed at 100% of equity (long-only)
    # Exit style: a trailing stop lets winners run (vs a fixed ATR take-profit).
    use_trailing_stop: bool = False
    trail_atr_mult: float = 3.0  # trail this many ATRs below the position's peak
    # Walk-forward window: only TRADE within [trade_start, trade_end] (ISO dates).
    # Indicators still warm up on all prior data, so each window is out-of-sample.
    trade_start: str | None = None
    trade_end: str | None = None


@dataclass
class _Position:
    shares: float
    entry: float
    stop: float
    target: float
    entry_atr: float = 0.0   # ATR at entry, used to size the trailing stop
    peak: float = 0.0        # highest price seen since entry


@dataclass
class Trade:
    symbol: str
    entry: float
    exit: float
    ret: float
    reason: str  # "stop" | "target" | "signal"


@dataclass
class BacktestResult:
    market: Market
    equity: pd.Series            # strategy equity curve
    benchmark: pd.Series         # index buy-and-hold, scaled to start_equity
    metrics: Metrics
    benchmark_metrics: Metrics
    trades: list[Trade] = field(default_factory=list)


class Backtester:
    def __init__(
        self,
        market: Market,
        provider: MarketDataProvider,
        cfg: SignalConfig | None = None,
        bt: BacktestConfig | None = None,
    ) -> None:
        self._market = market
        self._provider = provider
        self._cfg = cfg or SignalConfig()
        self._bt = bt or BacktestConfig()

    # ------------------------------ data prep ------------------------------

    def _load_panel(self) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        """Return (symbol -> OHLCV df indexed by date, index df)."""
        panel: dict[str, pd.DataFrame] = {}
        for sym in self._cfg.universe(self._market):
            bars = self._provider.get_history(
                sym, self._market, period=self._bt.period
            )
            if len(bars) >= self._bt.warmup_bars:
                df = ind.bars_to_frame(bars)
                df.index = df.index.normalize()
                panel[sym] = df
            else:
                log.info("Backtest: dropping %s (%d bars)", sym, len(bars))
        index_sym = self._cfg.index_symbol[self._market]
        idx_bars = self._provider.get_history(index_sym, Market.US, period=self._bt.period)
        idx_df = ind.bars_to_frame(idx_bars)
        idx_df.index = idx_df.index.normalize()
        return panel, idx_df

    # ------------------------------ scoring -------------------------------

    def _composite(self, sym_slice: pd.DataFrame, macro: float) -> tuple[float, object]:
        """Technical+macro composite (renormalised), plus the technical view."""
        cfg = self._cfg
        tech, view = technical_score(sym_slice, cfg)
        denom = cfg.w_technical + cfg.w_macro
        composite = (cfg.w_technical * tech.value + cfg.w_macro * macro) / denom
        return composite, view

    def _regime(self, idx_slice: pd.Series) -> tuple[float, bool]:
        cfg = self._cfg
        if len(idx_slice) < cfg.regime_sma:
            return 0.0, False
        sma = idx_slice.rolling(cfg.regime_sma).mean().iloc[-1]
        price = float(idx_slice.iloc[-1])
        roc = float(idx_slice.pct_change(20).iloc[-1] * 100.0)
        above = price > sma
        score = (0.6 if above else -0.6) + max(-0.4, min(0.4, roc / 15.0))
        return max(-1.0, min(1.0, score)), (not above)

    # -------------------------------- run ---------------------------------

    def run(self) -> BacktestResult:
        cfg, bt = self._cfg, self._bt
        panel, idx_df = self._load_panel()
        if not panel or idx_df.empty:
            raise RuntimeError(f"No backtest data for {self._market.value}")

        idx_close = idx_df["close"]
        dates = idx_close.index[bt.warmup_bars:]  # trade only after warmup
        # Restrict the TRADING window (walk-forward folds). Indicator slices below
        # still use the full history, so each window is genuinely out-of-sample.
        if bt.trade_start:
            ts = pd.Timestamp(bt.trade_start)
            dates = dates[dates >= (ts.tz_localize(dates.tz) if dates.tz else ts)]
        if bt.trade_end:
            te = pd.Timestamp(bt.trade_end)
            dates = dates[dates <= (te.tz_localize(dates.tz) if dates.tz else te)]
        if len(dates) == 0:
            raise RuntimeError(f"No trading dates in window for {self._market.value}")

        # Forward-filled close per symbol on the master calendar, so a held position
        # is ALWAYS valued at its last known price (never phantom-zeroed on a day the
        # stock has no bar). Using a real ffilled price keeps drawdown/Sharpe honest.
        vclose = {
            sym: df["close"].reindex(idx_close.index).ffill()
            for sym, df in panel.items()
        }

        cash = bt.start_equity
        positions: dict[str, _Position] = {}
        trades: list[Trade] = []
        equity_points: list[tuple[pd.Timestamp, float]] = []

        def exposure(day) -> float:
            total = 0.0
            for s, p in positions.items():
                px = vclose[s].get(day)
                if px is not None and px == px:  # not NaN
                    total += p.shares * float(px)
            return total

        for i, d in enumerate(dates):
            # 1) daily stop/target checks on open positions
            for sym in list(positions.keys()):
                sdf = panel.get(sym)
                if sdf is None or d not in sdf.index:
                    continue
                row = sdf.loc[d]
                pos = positions[sym]
                # Trailing stop: ratchet the stop up as the position makes new highs.
                if bt.use_trailing_stop:
                    pos.peak = max(pos.peak, float(row["high"]))
                    pos.stop = max(pos.stop, pos.peak - bt.trail_atr_mult * pos.entry_atr)
                exit_price = reason = None
                if row["low"] <= pos.stop:
                    exit_price = pos.stop if row["open"] >= pos.stop else float(row["open"])
                    reason = "stop"
                elif not bt.use_trailing_stop and row["high"] >= pos.target:
                    exit_price = pos.target if row["open"] <= pos.target else float(row["open"])
                    reason = "target"
                if exit_price is not None:
                    cash += pos.shares * exit_price * (1 - bt.commission_pct)
                    trades.append(Trade(sym, pos.entry, exit_price,
                                        exit_price / pos.entry - 1.0, reason))
                    del positions[sym]

            # 2) weekly rebalance: signal-based exits + new entries
            if i % bt.rebalance_every == 0:
                macro, risk_off = self._regime(idx_close.loc[:d])
                # signal exits
                for sym in list(positions.keys()):
                    sdf = panel.get(sym)
                    if sdf is None or d not in sdf.index:
                        continue
                    comp, _ = self._composite(sdf.loc[:d], macro)
                    if comp <= cfg.sell_threshold:
                        px = float(sdf.loc[d, "close"])
                        pos = positions[sym]
                        cash += pos.shares * px * (1 - bt.commission_pct)
                        trades.append(Trade(sym, pos.entry, px, px / pos.entry - 1.0, "signal"))
                        del positions[sym]
                # candidate entries (ranked by conviction)
                threshold = cfg.buy_threshold + (cfg.risk_off_buy_penalty if risk_off else 0.0)
                equity_now = cash + exposure(d)
                candidates = []
                for sym, sdf in panel.items():
                    if sym in positions or d not in sdf.index or len(sdf.loc[:d]) < bt.warmup_bars:
                        continue
                    comp, view = self._composite(sdf.loc[:d], macro)
                    if comp >= threshold:
                        candidates.append((comp, sym, view))
                candidates.sort(reverse=True, key=lambda c: c[0])
                for comp, sym, view in candidates:
                    invested = exposure(d)
                    if invested >= bt.max_invested * equity_now:
                        break
                    lv = buy_levels(view.price, view.atr, comp, cfg, risk_off=risk_off)
                    # Deploy toward the per-name cap (strongest names first) so capital
                    # isn't left idle in a bull market — the key drag the backtest exposed.
                    target_alloc = cfg.max_position_weight * equity_now
                    if risk_off:
                        target_alloc *= cfg.risk_off_size_factor
                    alloc = min(target_alloc, cash, (bt.max_invested * equity_now - invested))
                    if alloc <= 0 or view.price <= 0:
                        continue
                    shares = alloc / view.price
                    cash -= shares * view.price * (1 + bt.commission_pct)
                    positions[sym] = _Position(
                        shares, view.price, lv.stop_loss, lv.take_profit,
                        entry_atr=view.atr, peak=view.price,
                    )

            # 3) mark to market (forward-filled prices — no phantom zeros)
            equity_points.append((d, cash + exposure(d)))

        equity = pd.Series(dict(equity_points)).sort_index()
        bench = idx_close.loc[equity.index[0]:].reindex(equity.index).ffill()
        bench = bench / float(bench.iloc[0]) * bt.start_equity

        trade_rets = [t.ret for t in trades]
        return BacktestResult(
            market=self._market,
            equity=equity,
            benchmark=bench,
            metrics=compute_metrics(equity, trade_rets),
            benchmark_metrics=compute_metrics(bench, []),
            trades=trades,
        )
