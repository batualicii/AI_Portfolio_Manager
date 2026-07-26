"""Buy-and-hold *selection* backtester — pick names, then leave them alone.

The tactical engine in `engine.py` answers "when should I be in?". This answers a
different question: "which names should I hold?". The Stage 8 re-validation found
that holding the watchlist beat trading it in both markets, so the design worth
testing next is selection rather than timing.

Four deliberate differences from the tactical backtester:

  * **No stops and no targets.** Getting shaken out is precisely what destroys a
    hold strategy's return; a position is exited only when it drops out of the
    ranking at a rebalance.
  * **Quarterly rebalance**, not weekly. Selection signals move slowly.
  * **12-1 momentum**: total return over the last 12 months, skipping the most
    recent month. The skip is standard — the last month tends to mean-revert, so
    including it adds noise to a long-horizon signal.
  * **Equal weight** across the held names, so the result measures selection and
    not a sizing scheme layered on top.

The benchmark that matters is the equal-weight hold of the *same* universe. Both
sides then draw from the same survivorship-biased pool (SPEC section 6c), so the
bias largely cancels and what is left is selection skill.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from src.backtest.engine import _equal_weight_curve
from src.backtest.metrics import Metrics, compute_metrics
from src.market.provider import MarketDataProvider
from src.models import Market
from src.signals import indicators as ind
from src.signals.config import SignalConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HoldConfig:
    start_equity: float = 100_000.0
    period: str = "5y"
    top_n: int = 8                 # how many names to hold at once
    rebalance_days: int = 63       # ~one quarter of trading days
    momentum_lookback: int = 252   # ~12 months
    momentum_skip: int = 21        # ~1 month, dropped to avoid short-term reversal
    commission_pct: float = 0.001  # charged on traded notional, both directions
    trade_start: str | None = None
    trade_end: str | None = None

    @property
    def warmup_bars(self) -> int:
        # Need the full ranking window available before the first decision.
        return self.momentum_lookback + self.momentum_skip + 5


@dataclass
class HoldResult:
    market: Market
    equity: pd.Series
    benchmark: pd.Series             # index buy-and-hold
    universe_benchmark: pd.Series    # equal-weight hold of the same universe
    metrics: Metrics
    benchmark_metrics: Metrics
    universe_metrics: Metrics
    holdings_log: list[tuple[pd.Timestamp, list[str]]] = field(default_factory=list)
    turnover_pct: float = 0.0


def pit_equal_weight_curve(
    panel: dict[str, pd.DataFrame], index, members_at=None
) -> pd.Series:
    """Equal-weight index of whoever was a member on each date.

    `_equal_weight_curve` in engine.py holds every symbol in the panel for the
    whole window, which is the right benchmark when the universe is fixed. It is
    the wrong one here: the panel contains everyone who was *ever* a member, so
    holding all of them from the start would credit the benchmark with positions
    nobody could have owned yet.

    Chained daily equal-weight returns across the current members, with no
    trading costs — the convention index benchmarks are quoted under, and the
    conservative choice, since charging a passive alternative for quarterly
    rebalancing would flatter the strategy it is being compared against.
    """
    closes = pd.DataFrame({s: df["close"].reindex(index).ffill() for s, df in panel.items()})
    daily = closes.pct_change()

    if members_at is not None:
        mask = pd.DataFrame(False, index=index, columns=closes.columns)
        for day in index:
            eligible = members_at(day)
            for sym in closes.columns:
                if sym in eligible:
                    mask.loc[day, sym] = True
        daily = daily.where(mask)

    # Mean across whoever is eligible and priced on each day.
    port = daily.mean(axis=1, skipna=True).fillna(0.0)
    return (1.0 + port).cumprod()


def momentum_12_1(close: pd.Series, cfg: HoldConfig) -> float | None:
    """Total return from `lookback` bars ago to `skip` bars ago.

    None when there is not enough history — an absent score must never be
    silently read as a weak one.
    """
    needed = cfg.momentum_lookback + cfg.momentum_skip
    if len(close) < needed:
        return None
    start = float(close.iloc[-needed])
    end = float(close.iloc[-(cfg.momentum_skip + 1)])
    if start <= 0:
        return None
    return end / start - 1.0


Selector = "Callable[[list[tuple[float, str]], int], list[str]]"


def top_by_score(ranked: list[tuple[float, str]], top_n: int) -> list[str]:
    """Default selector: take the highest-scoring names."""
    ranked = sorted(ranked, reverse=True)
    return [sym for _, sym in ranked[:top_n]]


class HoldBacktester:
    def __init__(
        self,
        market: Market,
        provider: MarketDataProvider,
        cfg: SignalConfig | None = None,
        hold: HoldConfig | None = None,
        selector=None,
        members_at=None,
    ) -> None:
        self._market = market
        self._provider = provider
        self._cfg = cfg or SignalConfig()
        self._hold = hold or HoldConfig()
        # Swappable so the same simulation can be driven by a random pick, which
        # is how we measure whether the ranking beats luck within this universe.
        self._selector = selector or top_by_score
        # Optional date -> set[str] of index members on that date. Without it the
        # whole configured universe is eligible on every date, which means the
        # run silently assumes today's membership held in the past — the
        # survivorship bias described in SPEC section 6c. Supplying it restricts
        # each decision to the names actually available at the time.
        self._members_at = members_at

    def _load_panel(self) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        panel: dict[str, pd.DataFrame] = {}
        for sym in self._cfg.universe(self._market):
            bars = self._provider.get_history(sym, self._market, period=self._hold.period)
            if len(bars) >= self._hold.warmup_bars:
                df = ind.bars_to_frame(bars)
                df.index = df.index.normalize()
                panel[sym] = df
            else:
                log.info("Hold backtest: dropping %s (%d bars)", sym, len(bars))
        index_sym = self._cfg.index_symbol[self._market]
        idx = ind.bars_to_frame(
            self._provider.get_history(index_sym, Market.US, period=self._hold.period)
        )
        if not idx.empty:
            idx.index = idx.index.normalize()
        return panel, idx

    def run(self) -> HoldResult:
        hold = self._hold
        panel, idx_df = self._load_panel()
        if not panel or idx_df.empty:
            raise RuntimeError(f"No hold-backtest data for {self._market.value}")

        idx_close = idx_df["close"]
        dates = idx_close.index[hold.warmup_bars:]
        if hold.trade_start:
            ts = pd.Timestamp(hold.trade_start)
            dates = dates[dates >= (ts.tz_localize(dates.tz) if dates.tz else ts)]
        if hold.trade_end:
            te = pd.Timestamp(hold.trade_end)
            dates = dates[dates <= (te.tz_localize(dates.tz) if dates.tz else te)]
        if len(dates) == 0:
            raise RuntimeError(f"No trading dates in window for {self._market.value}")

        # Forward-filled closes on the master calendar, so a held name is always
        # valued at its last known price rather than dropping to nothing on a day
        # it happens not to trade.
        vclose = {s: df["close"].reindex(idx_close.index).ffill() for s, df in panel.items()}

        cash = hold.start_equity
        shares: dict[str, float] = {}
        equity_points: list[tuple[pd.Timestamp, float]] = []
        holdings_log: list[tuple[pd.Timestamp, list[str]]] = []
        traded_notional = 0.0

        def price(sym: str, day) -> float | None:
            px = vclose[sym].get(day)
            if px is None or px != px or px <= 0:  # None or NaN or nonsense
                return None
            return float(px)

        def portfolio_value(day) -> float:
            total = cash
            for sym, qty in shares.items():
                px = price(sym, day)
                if px is not None:
                    total += qty * px
            return total

        for i, day in enumerate(dates):
            if i % hold.rebalance_days == 0:
                eligible = self._members_at(day) if self._members_at else None
                ranked: list[tuple[float, str]] = []
                for sym, df in panel.items():
                    if eligible is not None and sym not in eligible:
                        continue  # not in the index on this date
                    window = df["close"].loc[:day]
                    score = momentum_12_1(window, hold)
                    if score is not None and price(sym, day) is not None:
                        ranked.append((score, sym))

                if ranked:
                    chosen = self._selector(ranked, hold.top_n)
                    equity_now = portfolio_value(day)
                    target_value = equity_now / len(chosen)

                    # Sell what dropped out of the ranking, then size every held
                    # name to an equal share of the book.
                    for sym in list(shares):
                        if sym not in chosen:
                            px = price(sym, day)
                            if px is not None:
                                proceeds = shares[sym] * px
                                traded_notional += proceeds
                                cash += proceeds * (1 - hold.commission_pct)
                                del shares[sym]

                    for sym in chosen:
                        px = price(sym, day)
                        if px is None:
                            continue
                        current = shares.get(sym, 0.0) * px
                        delta = target_value - current
                        if abs(delta) < target_value * 0.01:
                            continue  # skip trivial rebalancing churn
                        traded_notional += abs(delta)
                        if delta > 0 and cash >= delta:
                            cash -= delta * (1 + hold.commission_pct)
                            shares[sym] = shares.get(sym, 0.0) + delta / px
                        elif delta < 0:
                            cash += -delta * (1 - hold.commission_pct)
                            shares[sym] = max(shares.get(sym, 0.0) + delta / px, 0.0)

                    holdings_log.append((day, chosen))

            equity_points.append((day, portfolio_value(day)))

        equity = pd.Series(dict(equity_points)).sort_index()

        bench = idx_close.loc[equity.index[0]:].reindex(equity.index).ffill()
        bench = bench / float(bench.iloc[0]) * hold.start_equity

        # With membership supplied, the benchmark must respect it too — otherwise
        # the strategy is judged against a portfolio holding names that were not
        # yet in the index, which is the same bias the run exists to remove.
        if self._members_at is not None:
            universe = pit_equal_weight_curve(panel, equity.index, self._members_at)
        else:
            universe = _equal_weight_curve(panel, equity.index)
        universe = universe / float(universe.iloc[0]) * hold.start_equity

        return HoldResult(
            market=self._market,
            equity=equity,
            benchmark=bench,
            universe_benchmark=universe,
            metrics=compute_metrics(equity, []),
            benchmark_metrics=compute_metrics(bench, []),
            universe_metrics=compute_metrics(universe, []),
            holdings_log=holdings_log,
            turnover_pct=round(traded_notional / hold.start_equity * 100.0, 1),
        )
