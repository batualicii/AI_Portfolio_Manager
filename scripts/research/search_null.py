"""Search every indicator/weight combination — and measure what the search itself
would have found in data containing nothing.

This is the experiment the previous two attempts in this repo were missing, and
it is the reason both of them produced a "winner" that did not survive contact
with reality.

## The mistake it corrects

The natural way to search is: try M combinations, keep the best, check whether
the best beats zero. That test is wrong, and the size of the error is not small.
Try 2,000 combinations on data with **no signal whatsoever** and the best of them
will still look excellent — not because anything was found, but because the
maximum of 2,000 noisy draws is far from their mean. Comparing that maximum
against zero measures nothing except how many combinations were tried.

The correct comparison is against **the best obtainable from noise**. So this
runs the identical search on hundreds of worlds where the answer is known to be
nothing, and asks where the real search's winner falls in that distribution.
That is the multiple-comparisons correction, and without it a parameter sweep at
this sample size is a machine for generating false confidence.

Two outcomes, both worth having:

  * the real winner sits inside the null cloud → the search found nothing, and
    we now know that with a number instead of a suspicion. Every combination on
    the leaderboard is noise, including the ones that look wonderful.
  * the real winner sits beyond the 99th percentile of the null → the first
    genuine evidence this repo has produced, and it goes straight to a holdout.

## How the null is built

At each rebalance date, the mapping from symbol to its *forward* return is
randomly permuted among the names that were tradeable that day. This destroys
any relationship between a score and what follows it, while leaving completely
intact: the market's return that quarter, the cross-sectional dispersion, the
fat tails, the survivorship structure of the universe, and the equal-weight
benchmark. It is the same world with the predictability surgically removed —
which is exactly the counterfactual the question needs.

The scores never change between worlds, so the selection each combination makes
is computed once and reused. That is what makes a thousand null worlds cheap.

## What it deliberately cannot test

Only price-derived signals. There is no free point-in-time fundamentals history,
so book value, earnings and margins as they were *known on the day* are not
available, and using today's figures would leak the future into every backtest.
This limitation is old news in this repo (SPEC section 6c) and it caps what any
answer here is worth.

Usage:
    python -m scripts.research.search_null --build          # fetch + cache panel
    python -m scripts.research.search_null                  # search + null
    python -m scripts.research.search_null --samples 4000 --worlds 2000
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import time

import numpy as np
import pandas as pd

from src.market.yahoo import YahooProvider
from src.models import Market
from src.signals import indicators as ind

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
PIT = ROOT / "universes" / "sp500_pit.json"
CACHE = ROOT / "data" / "search_panel.npz"

log = logging.getLogger("search_null")


# --------------------------------------------------------------------------
# The signal library.
#
# Every entry has published cross-sectional evidence behind it; the sweep is
# not a fishing expedition over whatever an indicator library happens to
# expose. Two things about that evidence are worth carrying into the reading
# of any result:
#
#   * McLean & Pontiff (2016) measured that published anomaly returns fall by
#     roughly 58% after publication. These are all published.
#   * Hou, Xue & Zhang (2020) re-tested ~450 published anomalies under uniform
#     methodology; most did not replicate. Harvey, Liu & Zhu (2016) argue the
#     t-statistic threshold for a *new* factor should be about 3.0 rather than
#     2.0, precisely because of how much has already been searched.
#
# So the prior on finding something here is low before a single line runs, and
# the null distribution below is how that prior gets made concrete.
# --------------------------------------------------------------------------
SIGNALS = {
    # Jegadeesh & Titman (1993). The most replicated cross-sectional effect
    # there is, and still subject to violent crashes (Daniel & Moskowitz 2016).
    "mom_12_1": lambda c: c.shift(21) / c.shift(252) - 1.0,
    "mom_6_1":  lambda c: c.shift(21) / c.shift(126) - 1.0,
    "mom_3_1":  lambda c: c.shift(21) / c.shift(63) - 1.0,
    # Short-term reversal (Jegadeesh 1990) — sign flipped so higher is better.
    "reversal_1m": lambda c: -(c / c.shift(21) - 1.0),
    # Distance from the 200-day average: trend as a level rather than a return.
    "trend_200": lambda c: c / c.rolling(200).mean() - 1.0,
    # Betting-against-beta / low-volatility (Frazzini & Pedersen 2014).
    "low_vol": lambda c: -c.pct_change().rolling(126).std(),
    # Proximity to the 52-week high (George & Hwang 2004).
    "near_52w_high": lambda c: c / c.rolling(252).max(),
    # How deep its worst fall has been: a crude quality-of-ride measure, and
    # the one an owner of eight names actually has to live through.
    "shallow_dd": lambda c: (c / c.rolling(252).max() - 1.0).rolling(252).min(),
}
NAMES = tuple(SIGNALS)


def build_panel(period: str = "max") -> None:
    """Fetch once, compute every signal, cache. Slow; run with --build.

    Membership is read per year from `by_year`, not flattened. The first
    version of this function pulled `dropped_names` alone — 236 companies that
    *left* the index — and searched that, which is the failure sample rather
    than the universe. Reading the yearly lists both fixes that and makes the
    membership mask below possible.
    """
    data = json.loads(PIT.read_text())
    by_year = {int(y): set(v) for y, v in data["by_year"].items()}
    if not by_year:
        raise SystemExit("sp500_pit.json has no by_year membership; rebuild it")
    symbols = sorted({s for members in by_year.values() for s in members})
    log.warning("Universe: %d distinct symbols across %d years (%d–%d), "
                "~%d members per year",
                len(symbols), len(by_year), min(by_year), max(by_year),
                sum(len(v) for v in by_year.values()) // len(by_year))

    provider = YahooProvider()
    closes: dict[str, pd.Series] = {}
    for i, symbol in enumerate(symbols, 1):
        if i % 50 == 0:
            log.warning("  ...%d/%d", i, len(symbols))
        try:
            bars = provider.get_history(symbol, Market.US, period=period)
        except Exception:  # noqa: BLE001
            continue
        if len(bars) < 300:
            continue
        frame = ind.bars_to_frame(bars)
        closes[symbol] = frame["close"]

    panel = pd.DataFrame(closes).sort_index()
    log.warning("Priceable: %d symbols, %s to %s",
                panel.shape[1], panel.index[0].date(), panel.index[-1].date())

    # Quarterly rebalance dates — the last trading day of each quarter.
    marks = panel.resample("QE").last().index
    dates = [panel.index[panel.index <= m][-1] for m in marks
             if (panel.index <= m).any()]
    dates = [d for d in dates if (panel.index < d).sum() >= 260]

    # Point-in-time membership. Without this the search can buy a company in
    # 2016 that only entered the index in 2023 — selected, in effect, for having
    # gone on to succeed. That is the single largest bias available here, and
    # this repo has already measured it at 36.4 pp/yr.
    columns = list(panel.columns)
    eligible = np.zeros((len(dates), len(columns)), dtype=bool)
    years = sorted(by_year)
    for t, day in enumerate(dates):
        year = min(max(day.year, years[0]), years[-1])
        members = by_year[year]
        eligible[t] = np.array([s in members for s in columns])

    scores = np.full((len(NAMES), len(dates), panel.shape[1]), np.nan)
    for k, name in enumerate(NAMES):
        series = SIGNALS[name](panel)
        for t, day in enumerate(dates):
            row = series.loc[day].to_numpy(dtype=float)
            scores[k, t] = np.where(eligible[t], row, np.nan)

    # Forward return to the next rebalance, which is what selection is judged on.
    forward = np.full((len(dates), panel.shape[1]), np.nan)
    for t in range(len(dates) - 1):
        a, b = panel.loc[dates[t]].to_numpy(), panel.loc[dates[t + 1]].to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            step = b / a - 1.0
        # A name that left the index still has a price, and its return still
        # counts for anyone holding it — but only members are selectable, so the
        # benchmark must be the members too, or real and null would be measured
        # against different universes.
        forward[t] = np.where(eligible[t], step, np.nan)

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        CACHE, scores=scores, forward=forward, eligible=eligible,
        symbols=np.array(panel.columns), dates=np.array([str(d.date()) for d in dates]),
        signal_names=np.array(NAMES),
    )
    log.warning("Cached %s — %d dates, %d names", CACHE.name, len(dates),
                panel.shape[1])


def _rank_normalise(scores: np.ndarray) -> np.ndarray:
    """Cross-sectional percentile rank per (signal, date), NaN-safe.

    Ranks rather than z-scores so that one company's insane P/E or a 900%
    momentum print cannot dominate a weighted sum, and so weights across
    signals are on a common scale and therefore comparable at all.
    """
    out = np.full_like(scores, np.nan)
    for k in range(scores.shape[0]):
        for t in range(scores.shape[1]):
            row = scores[k, t]
            ok = ~np.isnan(row)
            if ok.sum() < 30:
                continue
            order = row[ok].argsort().argsort().astype(float)
            out[k, t, ok] = order / max(order.max(), 1.0)
    return out


def _sample_weights(rng: np.random.Generator, m: int, k: int) -> np.ndarray:
    """Random points on the simplex, plus every single-signal corner.

    Random search beats a grid badly in this many dimensions (Bergstra & Bengio
    2012), and the corners are included so the report can always say how each
    signal does alone — a combination that cannot beat its own best ingredient
    is not a combination worth having.
    """
    corners = np.eye(k)
    equal = np.full((1, k), 1.0 / k)
    # Dirichlet(0.6) leans towards sparse mixtures: most real combinations that
    # anyone would actually run put weight on two or three things, not nine.
    drawn = rng.dirichlet(np.full(k, 0.6), size=max(m - k - 1, 1))
    return np.vstack([corners, equal, drawn])


def _select(composite: np.ndarray, top_n: int) -> np.ndarray:
    """Indices of the top `top_n` names per (combination, date)."""
    filled = np.where(np.isnan(composite), -np.inf, composite)
    return np.argpartition(-filled, top_n - 1, axis=-1)[..., :top_n]


def evaluate(picks: np.ndarray, forward: np.ndarray) -> np.ndarray:
    """Annualised excess return over the equal-weight universe, per combination.

    Excess over equal weight, not raw return, because the null permutes symbols
    within a date and therefore leaves the equal-weight benchmark untouched —
    which makes the comparison between real and null exactly like-for-like.
    """
    m, t, _ = picks.shape
    valid = ~np.isnan(forward)
    bench = np.array([forward[i][valid[i]].mean() if valid[i].any() else np.nan
                      for i in range(t)])

    out = np.empty(m)
    for i in range(m):
        taken = np.take_along_axis(forward[None, :, :], picks[i][None], axis=-1)[0]
        # A basket can come out entirely unpriceable on a thin date. nanmean
        # would warn and return NaN; counting the names first says the same
        # thing without pretending an empty average happened.
        filled = np.where(np.isnan(taken), 0.0, taken).sum(axis=-1)
        held = (~np.isnan(taken)).sum(axis=-1)
        port = np.where(held > 0, filled / np.maximum(held, 1), np.nan)
        excess = port - bench
        good = ~np.isnan(excess)
        # Quarterly rebalance, so four periods a year.
        out[i] = excess[good].mean() * 4 if good.any() else np.nan
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", action="store_true", help="fetch and cache first")
    parser.add_argument("--samples", type=int, default=2000)
    parser.add_argument("--worlds", type=int, default=1000)
    parser.add_argument("--top", type=int, nargs="+", default=[5, 8, 12, 20])
    parser.add_argument("--holdout-years", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260804)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if args.build or not CACHE.exists():
        build_panel()
    if not CACHE.exists():
        print("no panel — run with --build")
        return 1

    blob = np.load(CACHE, allow_pickle=True)
    scores, forward = blob["scores"], blob["forward"]
    dates = [str(d) for d in blob["dates"]]
    names = [str(s) for s in blob["signal_names"]]
    rng = np.random.default_rng(args.seed)

    # The holdout is carved off before anything is searched and is not touched
    # again until one frozen winner is applied to it, once.
    split = max(len(dates) - args.holdout_years * 4, 4)
    log.warning("\nTrain %s .. %s   |   Holdout %s .. %s  (untouched)",
                dates[0], dates[split - 1], dates[split], dates[-1])

    ranked = _rank_normalise(scores)
    weights = _sample_weights(rng, args.samples, len(names))
    log.warning("Searching %d weight vectors x %d basket sizes = %d combinations",
                len(weights), len(args.top), len(weights) * len(args.top))

    def run(lo: int, hi: int, shuffled: np.ndarray | None = None):
        """Score every combination over dates[lo:hi]. Returns (values, picks)."""
        window = ranked[:, lo:hi, :]
        fwd = forward[lo:hi] if shuffled is None else shuffled
        k, t, n = window.shape
        composite = (weights @ window.reshape(k, -1)).reshape(-1, t, n)
        values, all_picks = [], []
        for top_n in args.top:
            picks = _select(composite, top_n)
            values.append(evaluate(picks, fwd))
            all_picks.append(picks)
        return np.array(values), all_picks

    started = time.time()
    real, picks_by_top = run(0, split)
    best_flat = int(np.nanargmax(real))
    best_top_i, best_w = divmod(best_flat, real.shape[1])
    best_value = real[best_top_i, best_w]

    print(f"\n{'=' * 78}\nSEARCH — best of {real.size} combinations\n{'=' * 78}")
    print(f"  best excess over equal weight : {best_value * 100:+.2f} pp / yr")
    print(f"  basket size                   : {args.top[best_top_i]} names")
    print("  weights                       : " + ", ".join(
        f"{n} {w:.2f}" for n, w in zip(names, weights[best_w]) if w > 0.02))

    print("\n  each signal on its own (equal-weight universe = 0):")
    for k, name in enumerate(names):
        print(f"    {name:<16} {real[best_top_i, k] * 100:+6.2f} pp/yr")

    # ---------------------------------------------------------------- null
    # The scores are identical in every world, so the selections are too, and
    # only the forward returns are re-drawn. That is what makes this cheap.
    print(f"\n{'=' * 78}\nNULL — the same search, on {args.worlds} worlds with no "
          f"signal in them\n{'=' * 78}")
    train_fwd = forward[:split]
    null_best = np.empty(args.worlds)
    for w in range(args.worlds):
        if w % 200 == 0 and w:
            print(f"    ...{w}/{args.worlds}", flush=True)
        shuffled = train_fwd.copy()
        for t in range(shuffled.shape[0]):
            ok = np.flatnonzero(~np.isnan(shuffled[t]))
            shuffled[t, ok] = shuffled[t, rng.permutation(ok)]
        world = np.array([evaluate(p, shuffled) for p in picks_by_top])
        null_best[w] = np.nanmax(world)

    beaten = int((null_best >= best_value).sum())
    pct = float((null_best < best_value).mean() * 100)
    null_median = float(np.median(null_best))
    p95, p99 = np.percentile(null_best, [95, 99])
    # The percentile is itself an estimate from a finite number of worlds. Its
    # Monte Carlo error is binomial: with `beaten` worlds above the winner, the
    # count carries a standard error of about sqrt(beaten).
    pct_se = (max(beaten, 1) ** 0.5) / args.worlds * 100
    print(f"\n  best-from-nothing, median     : {null_median * 100:+.2f} pp/yr")
    print(f"  best-from-nothing, 95th pct   : {p95 * 100:+.2f} pp/yr")
    print(f"  best-from-nothing, 99th pct   : {p99 * 100:+.2f} pp/yr")
    print(f"  our winner                    : {best_value * 100:+.2f} pp/yr")
    print(f"  its percentile in the null    : {pct:.1f} ± {pct_se:.1f}  "
          f"({beaten}/{args.worlds} null worlds matched or beat it)")
    print("\n  That ± is the noise in the percentile from using a finite number "
          "of\n  worlds, not the noise in the finding. The permutation draw itself "
          "is\n  fixed by --seed; re-run with a different one for an independent "
          "cloud.")

    print(f"\n{'-' * 78}")
    if best_value <= p95:
        print("  VERDICT: nothing found.\n"
              "  A search of this size produces a winner this good from data with\n"
              "  no signal in it more than 5% of the time. Every combination on the\n"
              "  leaderboard is consistent with noise, including the best one, and\n"
              "  tuning further only searches harder against the same wall.")
    elif best_value <= p99:
        print("  VERDICT: suggestive, not established.\n"
              "  Beyond the 95th percentile of pure noise but inside the 99th. This\n"
              "  is the zone where a result is worth a holdout and worth nothing\n"
              "  else — do not act on it, and do not tune it further, which would\n"
              "  spend the evidence you just bought.")
    else:
        print("  VERDICT: beyond what this search finds in noise.\n"
              "  The first result in this repo to clear its own null. Apply the\n"
              "  frozen winner to the holdout below, once, and change nothing after\n"
              "  seeing it.")
    print(f"{'-' * 78}")

    # ------------------------------------------------------------- holdout
    holdout, _ = run(split, len(dates))
    held = holdout[best_top_i, best_w]
    print(f"\n{'=' * 78}\nHOLDOUT — frozen winner on {dates[split]} .. {dates[-1]}, "
          f"never searched\n{'=' * 78}")
    print(f"  train                         {best_value * 100:+.2f} pp/yr")
    print(f"  holdout                       {held * 100:+.2f} pp/yr")
    print(f"  what noise typically yields   {null_median * 100:+.2f} pp/yr "
          f"(median best-from-nothing)")

    # The comparison that decides it, printed rather than left for the reader to
    # make by putting two tables side by side.
    if held < null_median:
        print("\n  The frozen winner, on data it never saw, came in BELOW what this\n"
              "  same search typically extracts from pure noise. Whatever the\n"
              "  training number looked like, there is nothing here to act on.\n"
              "  Read this line before the percentile above: a suggestive null\n"
              "  percentile and a holdout under the null median together mean the\n"
              "  search fitted the training window.")
    elif held < best_value * 0.5:
        print("\n  Less than half the training figure survived. That decay is the\n"
              "  signature of a fitted result and is the normal outcome.")
    else:
        print("\n  The holdout kept most of the training figure. Rare, and still one\n"
              "  noisy number — a check on the search, not a second measurement.")
    print("\n  Either way the holdout is now spent. Tuning anything after seeing\n"
          "  it converts the last independent evidence into more training data.")

    # --------------------------------------------------- what it would take
    print(f"\n{'=' * 78}\nWHAT ANY OF THIS WOULD TAKE TO CONFIRM LIVE\n{'=' * 78}")
    for edge in (best_value, 0.03):
        # SPEC section 6c: an eight-name book carries ~13.3 pp of annual
        # tracking error against its benchmark.
        te = 0.133
        years = (2 * te / max(abs(edge), 1e-6)) ** 2
        print(f"  an edge of {edge * 100:+.1f} pp/yr needs ~{years:.0f} years of live "
              f"trading to distinguish from zero")
    print("\n  That number does not improve by searching harder. It is set by the\n"
          "  size of the book, and it is the reason this file reports a verdict\n"
          "  rather than a recommended parameter set.")

    print(f"\n  ({time.time() - started:.0f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
