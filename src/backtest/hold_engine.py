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

The benchmark that matters is the equal-weight hold of the *same* universe, so
both sides draw from the same pool. That is necessary but **not** sufficient here:
survivorship bias does not cancel between them. A static equal-weight holder is
barely affected by it, while a momentum selector concentrates precisely into the
names whose survival was guaranteed by construction. Removing it therefore needs
point-in-time membership (`members_at`), not just a shared universe — see
`pit_equal_weight_curve` below and SPEC section 6c.
"""
from __future__ import annotations

import dataclasses
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
    # Equal-weight rebalancing is itself a form of selling winners: it trims
    # whatever grew and tops up whatever lagged. That is the right default for
    # measuring a selection rule, and the wrong one for letting a position
    # compound, so the exit-rule experiments turn it off.
    rebalance_weights: bool = True
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
    outcomes: list[PositionOutcome] = field(default_factory=list)
    turnover_pct: float = 0.0
    # How much of the configured universe actually made it into the panel. A
    # provider that rate-limits will silently return nothing for some symbols,
    # which changes the universe between runs and therefore the result. Two runs
    # that disagree should be visibly different, not mysteriously different.
    universe_requested: int = 0
    universe_loaded: int = 0


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


Scorer = "Callable[[pd.Series, HoldConfig], float | None]"


def extension_pct(close: pd.Series, ma_window: int = 200) -> float | None:
    """How far above its moving average the price sits, as a fraction.

    The plain reading of "is this name already extended". 0.10 means 10% above
    the 200-day average; a name grinding along its average reads near zero, and
    one that has gone vertical reads high.
    """
    if len(close) < ma_window:
        return None
    avg = float(close.iloc[-ma_window:].mean())
    if avg <= 0:
        return None
    return float(close.iloc[-1]) / avg - 1.0


def not_yet_extended(max_extension: float = 0.25, ma_window: int = 200,
                     inner=momentum_12_1):
    """Momentum, but only for names that have not already gone vertical.

    12-1 momentum ranks by what has *already* risen most over a year, so on its
    own it systematically buys trends late — the opposite of holding a company
    from before the move. This wraps it with the cheapest available measure of
    "how late": distance above the 200-day average. A name still near its
    average is early in a trend; one far above it has had the move.

    Returns None for the extended names, which the engine reads as "no score",
    so they drop out of the ranking rather than being ranked lower — an
    ineligible name and a weak one must not be confused.

    Note what this cannot do: nothing here knows whether a company is well run
    or has a large market ahead of it. Historical fundamentals are not available
    for free (SPEC section 6c), so the fundamental half of "pick the next NVDA"
    is untested, not implemented. This is the price-based half only.
    """
    def score(close: pd.Series, cfg: HoldConfig) -> float | None:
        base = inner(close, cfg)
        if base is None:
            return None
        ext = extension_pct(close, ma_window)
        if ext is None or ext > max_extension:
            return None
        return base

    return score


def momentum_over(lookback: int, skip: int = 21, inner=momentum_12_1):
    """The same momentum measure over a different window.

    A shorter window reacts to a trend sooner, at the cost of catching more
    noise; testing 3, 6 and 12 months is how you find out which side of that
    trade-off this universe actually rewards.
    """
    def score(close: pd.Series, cfg: HoldConfig) -> float | None:
        return inner(close, dataclasses.replace(
            cfg, momentum_lookback=lookback, momentum_skip=skip))

    return score


@dataclass(frozen=True)
class ExitContext:
    """Everything an exit rule is allowed to look at, for one held name."""
    symbol: str
    day: pd.Timestamp
    close: pd.Series          # closes up to and including `day` — never beyond
    entry_day: pd.Timestamp
    entry_price: float
    price: float              # today's close


ExitRule = "Callable[[ExitContext], bool]"


def rank_drop_exit(ctx: ExitContext) -> bool:
    """Default: sell anything that fell out of the ranking. Rotation."""
    return True


def never_exit(ctx: ExitContext) -> bool:
    """Buy once and hold, whatever happens. The upper bound on letting winners run."""
    return False


def trend_break_exit(ma_window: int = 200, weeks: int = 4):
    """Exit only when the trend is genuinely gone, not when the price merely fell.

    A position must survive deep drawdowns to compound into something large — NVDA
    fell 56% in 2018 and 66% in 2022 on its way up. A rule that rotates quarterly
    exits at exactly those points, which is why it can never produce that outcome.
    Requiring a sustained break below the long moving average tolerates the
    drawdown while still cutting a name whose story is actually over.
    """
    bars = max(1, weeks * 5)

    def rule(ctx: ExitContext) -> bool:
        if len(ctx.close) < ma_window + bars:
            return False  # not enough history to judge; holding is the safer default
        avg = ctx.close.rolling(ma_window).mean()
        recent = ctx.close.iloc[-bars:] < avg.iloc[-bars:]
        return bool(recent.all())

    return rule


@dataclass(frozen=True)
class PositionOutcome:
    """One position's life, so results can be read as a distribution.

    The mean edge of an 8-name book is not measurable (SPEC section 6c), so the
    useful question is no longer "what was the average" but "what did the best
    position do, and how many went nowhere".
    """
    symbol: str
    entry_day: pd.Timestamp
    exit_day: pd.Timestamp | None     # None = still held at the end of the window
    entry_price: float
    exit_price: float
    contribution: float               # money: proceeds - cost, net of adds and trims

    @property
    def multiple(self) -> float:
        return self.exit_price / self.entry_price if self.entry_price > 0 else 0.0


Selector = "Callable[[list[tuple[float, str]], int], list[str]]"


def top_by_score(ranked: list[tuple[float, str]], top_n: int) -> list[str]:
    """Default selector: take the highest-scoring names."""
    ranked = sorted(ranked, reverse=True)
    return [sym for _, sym in ranked[:top_n]]


UNKNOWN_POLICIES = ("own", "shared", "exclude")


def sector_capped_selector(
    sectors: dict[str, str], max_per_sector: int = 2, unknown_policy: str = "own"
):
    """Take the highest-scoring names, but no more than `max_per_sector` per sector.

    A 12-1 momentum ranking has no notion of what a company does, so in a strong
    sectoral trend it will happily fill the whole book with one industry. The
    return that follows is then a bet on that industry, not evidence that the
    ranking picks companies well — and the two are indistinguishable until the
    concentration is removed.

    Capping per sector is the cheapest way to tell them apart: if the edge
    survives, the ranking is doing something; if it disappears, the edge *was*
    the sector.

    **What to do with symbols missing from the map decides the answer**, so it is
    a parameter rather than a default buried in the code. The names that cannot be
    sectored are almost exactly the ones that dropped out of the index — the group
    point-in-time membership exists to put back — so any handling of them tilts
    the result in a knowable direction:

      ``own``     each unmapped symbol gets its own bucket, so the cap never binds
                  on them. The cap still bites where the suspicion lives (a
                  concentration among mapped, currently-listed names), and the
                  restored dropouts are left alone. Where unmapped names dominate
                  a year, the capped leg simply stops differing from the uncapped
                  one — uninformative, which is far better than biased.
      ``shared``  all unmapped symbols compete for one bucket's slots. This
                  suppresses exposure to precisely the honest half of the universe,
                  pulling the result back toward the survivorship-inflated answer.
                  Pessimistic bound.
      ``exclude`` unmapped symbols cannot be held at all. That re-deletes the
                  dropouts, which is the original bias. Optimistic bound.

    Run all three and the truth is bracketed: if the verdict is the same under
    each, the coverage gap did not decide it.
    """
    if unknown_policy not in UNKNOWN_POLICIES:
        raise ValueError(f"unknown_policy must be one of {UNKNOWN_POLICIES}")

    def bucket_for(sym: str) -> str | None:
        sector = sectors.get(sym)
        if sector:
            return sector
        if unknown_policy == "exclude":
            return None
        return "Unknown" if unknown_policy == "shared" else f"\0unmapped\0{sym}"

    def pick(ranked: list[tuple[float, str]], top_n: int) -> list[str]:
        chosen: list[str] = []
        used: dict[str, int] = {}
        for _, sym in sorted(ranked, reverse=True):
            bucket = bucket_for(sym)
            if bucket is None or used.get(bucket, 0) >= max_per_sector:
                continue
            chosen.append(sym)
            used[bucket] = used.get(bucket, 0) + 1
            if len(chosen) >= top_n:
                break
        return chosen

    return pick


class HoldBacktester:
    def __init__(
        self,
        market: Market,
        provider: MarketDataProvider,
        cfg: SignalConfig | None = None,
        hold: HoldConfig | None = None,
        selector=None,
        members_at=None,
        scorer=None,
        exit_rule=None,
        panel_cache=None,
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
        # Swappable so a different definition of "worth holding" can be tested
        # without a second engine. The default ranks by 12-1 momentum, which
        # buys trends late by construction; see `not_yet_extended`.
        self._scorer = scorer or momentum_12_1
        # Asked only about names that dropped out of the ranking. The default
        # sells them, which is rotation; alternatives let a position ride.
        self._exit_rule = exit_rule or rank_drop_exit
        # Optional shared dict so a caller running the same universe many times
        # (cohort sweeps, rule comparisons) pays the panel-building cost once.
        # Passed in rather than global, so nothing is cached behind a caller's
        # back and tests stay independent of each other.
        self._panel_cache = panel_cache

    def _load_panel(self) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
        key = (self._market, self._cfg.universe(self._market), self._hold.period,
               self._hold.warmup_bars)
        if self._panel_cache is not None and key in self._panel_cache:
            return self._panel_cache[key]

        panel: dict[str, pd.DataFrame] = {}
        dropped: list[str] = []
        for sym in self._cfg.universe(self._market):
            bars = self._provider.get_history(sym, self._market, period=self._hold.period)
            if len(bars) >= self._hold.warmup_bars:
                df = ind.bars_to_frame(bars)
                df.index = df.index.normalize()
                panel[sym] = df
            else:
                dropped.append(sym)
        if dropped:
            # WARNING, not INFO: the scripts run at WARNING, and a silently
            # shrinking universe is exactly the kind of thing that makes two
            # runs disagree for no visible reason.
            log.warning(
                "Hold backtest: %d of %d symbols had too little history and were "
                "dropped (first few: %s)",
                len(dropped), len(self._cfg.universe(self._market)),
                ", ".join(dropped[:8]),
            )
        index_sym = self._cfg.index_symbol[self._market]
        idx = ind.bars_to_frame(
            self._provider.get_history(index_sym, Market.US, period=self._hold.period)
        )
        if not idx.empty:
            idx.index = idx.index.normalize()
        if self._panel_cache is not None:
            self._panel_cache[key] = (panel, idx)
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

        # Per-position bookkeeping, so the run can be read as a distribution of
        # outcomes rather than a single average (SPEC section 6c: the average is
        # not measurable at this basket size, but the spread is informative).
        entries: dict[str, tuple[pd.Timestamp, float]] = {}
        cost: dict[str, float] = {}
        proceeds_by_sym: dict[str, float] = {}
        outcomes: list[PositionOutcome] = []

        def open_position(sym: str, day, px: float) -> None:
            entries.setdefault(sym, (day, px))

        def close_position(sym: str, day, px: float, proceeds: float) -> None:
            entry_day, entry_px = entries.pop(sym, (day, px))
            got = proceeds_by_sym.pop(sym, 0.0) + proceeds
            outcomes.append(PositionOutcome(
                symbol=sym, entry_day=entry_day, exit_day=day,
                entry_price=entry_px, exit_price=px,
                contribution=got - cost.pop(sym, 0.0),
            ))

        for i, day in enumerate(dates):
            if i % hold.rebalance_days == 0:
                eligible = self._members_at(day) if self._members_at else None
                ranked: list[tuple[float, str]] = []
                for sym, df in panel.items():
                    if eligible is not None and sym not in eligible:
                        continue  # not in the index on this date
                    window = df["close"].loc[:day]
                    score = self._scorer(window, hold)
                    if score is not None and price(sym, day) is not None:
                        ranked.append((score, sym))

                if ranked:
                    chosen = self._selector(ranked, hold.top_n)

                    # A name that fell out of the ranking is offered to the exit
                    # rule rather than sold outright. The default sells it, which
                    # is rotation; other rules let the position keep running.
                    kept: list[str] = []
                    for sym in list(shares):
                        if sym in chosen:
                            continue
                        px = price(sym, day)
                        if px is None:
                            continue
                        entry_day, entry_px = entries[sym]
                        leaving = self._exit_rule(ExitContext(
                            symbol=sym, day=day, close=panel[sym]["close"].loc[:day],
                            entry_day=entry_day, entry_price=entry_px, price=px,
                        ))
                        if not leaving:
                            kept.append(sym)
                            continue
                        proceeds = shares[sym] * px
                        traded_notional += proceeds
                        cash += proceeds * (1 - hold.commission_pct)
                        close_position(sym, day, px, proceeds)
                        del shares[sym]

                    if hold.rebalance_weights:
                        book = chosen + kept
                        equity_now = portfolio_value(day)
                        target_value = equity_now / len(book)
                        for sym in book:
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
                                open_position(sym, day, px)
                                shares[sym] = shares.get(sym, 0.0) + delta / px
                                cost[sym] = cost.get(sym, 0.0) + delta
                            elif delta < 0:
                                cash += -delta * (1 - hold.commission_pct)
                                shares[sym] = max(shares.get(sym, 0.0) + delta / px, 0.0)
                                cost[sym] = cost.get(sym, 0.0) + delta
                    else:
                        # Existing positions are left completely alone, so a winner
                        # compounds instead of being trimmed back to equal weight.
                        # Only genuinely new names are bought, out of spare cash.
                        new = [s for s in chosen if s not in shares]
                        if new and cash > 0:
                            budget = cash / len(new)
                            for sym in new:
                                px = price(sym, day)
                                if px is None or cash < budget:
                                    continue
                                spend = budget / (1 + hold.commission_pct)
                                cash -= budget
                                traded_notional += spend
                                open_position(sym, day, px)
                                shares[sym] = shares.get(sym, 0.0) + spend / px
                                cost[sym] = cost.get(sym, 0.0) + spend

                    holdings_log.append((day, chosen + kept))

            equity_points.append((day, portfolio_value(day)))

        # Positions still open when the window ends are outcomes too — leaving
        # them out would drop exactly the winners a hold rule is meant to keep.
        last_day = dates[-1]
        for sym in list(shares):
            px = price(sym, last_day)
            if px is None:
                continue
            entry_day, entry_px = entries.get(sym, (last_day, px))
            outcomes.append(PositionOutcome(
                symbol=sym, entry_day=entry_day, exit_day=None,
                entry_price=entry_px, exit_price=px,
                contribution=(shares[sym] * px + proceeds_by_sym.get(sym, 0.0)
                              - cost.get(sym, 0.0)),
            ))

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
            outcomes=outcomes,
            universe_requested=len(self._cfg.universe(self._market)),
            universe_loaded=len(panel),
            turnover_pct=round(traded_notional / hold.start_equity * 100.0, 1),
        )
