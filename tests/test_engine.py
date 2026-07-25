"""SignalEngine — data in, ranked Recommendations out.

Covers the decision boundaries (BUY/SELL/TRIM/HOLD), the regime filter, the
long-only guarantee, and the ranking order the digest depends on.
"""
from __future__ import annotations

import dataclasses

import pytest

from src.models import Action, Holding, Market, Recommendation
from src.signals.config import SignalConfig
from src.signals.engine import SignalEngine, _rank_key
from src.signals.indicators import bars_to_frame
from src.signals.scoring import technical_score
from tests.conftest import (
    FakeNews,
    FakeProvider,
    choppy,
    flat,
    momentum_downtrend,
    momentum_uptrend,
    uptrend,
)

SMALL_UNIVERSE = dataclasses.replace(
    SignalConfig(), us_universe=("AAA",), bist_universe=("BBB",)
)


def _engine(provider, cfg=SMALL_UNIVERSE, news=None) -> SignalEngine:
    return SignalEngine(provider, news or FakeNews(), cfg)


def _with_index(provider: FakeProvider, cfg: SignalConfig, bars) -> FakeProvider:
    """Seed both regime indices. The engine looks them up under Market.US so the
    BIST ".IS" suffix is not appended twice — mirror that here."""
    for symbol in cfg.index_symbol.values():
        provider.set_history(symbol, Market.US, bars)
    return provider


# -------------------------------- regime ----------------------------------

def test_regime_is_risk_on_when_the_index_is_above_its_long_average():
    provider = _with_index(FakeProvider(), SMALL_UNIVERSE, momentum_uptrend())
    regime = _engine(provider).regime(Market.US)
    assert regime.risk_off is False
    assert regime.score > 0
    assert "above" in regime.note


def test_regime_is_risk_off_when_the_index_is_below_its_long_average():
    provider = _with_index(FakeProvider(), SMALL_UNIVERSE, momentum_downtrend())
    regime = _engine(provider).regime(Market.US)
    assert regime.risk_off is True
    assert regime.score < 0


def test_regime_is_neutral_when_index_history_is_missing():
    regime = _engine(FakeProvider()).regime(Market.US)
    assert regime.risk_off is False
    assert regime.score == 0.0
    assert regime.note == "regime unknown"


# ------------------------------- analyze ----------------------------------

def test_a_symbol_with_too_little_history_is_skipped():
    provider = FakeProvider()
    provider.set_history("AAA", Market.US, uptrend(10))  # below _MIN_BARS
    assert _engine(provider).analyze("AAA", Market.US) is None


def test_a_weak_candidate_is_not_reported_as_a_buy():
    provider = _with_index(FakeProvider(), SMALL_UNIVERSE, momentum_downtrend())
    provider.set_history("AAA", Market.US, momentum_downtrend())
    assert _engine(provider).analyze("AAA", Market.US) is None


def test_a_qualifying_candidate_becomes_a_buy_with_full_levels():
    cfg = dataclasses.replace(SMALL_UNIVERSE, buy_threshold=-1.0)  # force the branch
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    provider.set_history("AAA", Market.US, momentum_uptrend())

    reco = _engine(provider, cfg).analyze("AAA", Market.US)

    assert reco is not None and reco.action is Action.BUY
    # SPEC section 1: a BUY is only actionable with a stop, target and size.
    assert reco.stop_loss is not None and reco.stop_loss < reco.price
    assert reco.take_profit is not None and reco.take_profit > reco.price
    assert reco.suggested_weight is not None and 0 < reco.suggested_weight <= cfg.max_position_weight
    assert reco.rationale  # deterministic notes, before any narration


def test_a_held_name_that_breaks_down_is_a_sell_with_no_levels():
    cfg = dataclasses.replace(SMALL_UNIVERSE, sell_threshold=-0.15)
    provider = _with_index(FakeProvider(), cfg, momentum_downtrend())
    provider.set_history("AAA", Market.US, momentum_downtrend())
    holding = Holding("AAA", Market.US, 10, 100.0)

    reco = _engine(provider, cfg).analyze("AAA", Market.US, holding=holding)

    assert reco is not None and reco.action is Action.SELL
    # Selling out entirely means there is nothing left to protect or size.
    assert reco.stop_loss is None and reco.take_profit is None
    assert reco.suggested_weight is None


def test_a_healthy_held_name_is_a_hold_that_still_carries_a_stop():
    cfg = dataclasses.replace(SMALL_UNIVERSE, trim_threshold=-1.0, sell_threshold=-1.5)
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    provider.set_history("AAA", Market.US, momentum_uptrend())
    holding = Holding("AAA", Market.US, 10, 100.0)

    reco = _engine(provider, cfg).analyze("AAA", Market.US, holding=holding)

    assert reco is not None and reco.action is Action.HOLD
    assert reco.stop_loss is not None      # a held position always has a stop
    assert reco.take_profit is None


def test_a_held_name_whose_trailing_stop_is_breached_becomes_a_sell():
    """A mandatory stop is not advisory.

    A position that has round-tripped a rally can still score above the sell
    threshold — momentum stays positive as the fall decelerates. If the stop is
    taken out, that is the exit signal regardless of the composite, which is the
    entire reason SPEC section 1 requires one on every position.
    """
    cfg = dataclasses.replace(SMALL_UNIVERSE, sell_threshold=-0.99, trim_threshold=-0.98)
    # Rally then give it all back: the recent high is far above the last price.
    bars = momentum_uptrend(260) + momentum_downtrend(40, start=400.0, daily_pct=0.03)
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    provider.set_history("AAA", Market.US, bars)
    holding = Holding("AAA", Market.US, 10, 100.0)

    reco = _engine(provider, cfg).analyze("AAA", Market.US, holding=holding)

    assert reco is not None and reco.action is Action.SELL
    assert "trailing stop" in reco.rationale


def test_a_healthy_holds_stop_trails_below_its_recent_high():
    cfg = dataclasses.replace(SMALL_UNIVERSE, trim_threshold=-1.0, sell_threshold=-1.5)
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    provider.set_history("AAA", Market.US, momentum_uptrend())
    holding = Holding("AAA", Market.US, 10, 100.0)

    reco = _engine(provider, cfg).analyze("AAA", Market.US, holding=holding)

    assert reco is not None and reco.stop_loss is not None
    assert reco.stop_loss < reco.price      # still a protective level
    assert reco.action is Action.HOLD       # not breached


def test_mild_weakness_in_a_held_name_is_a_trim_not_a_sell():
    # Squeeze the thresholds so a flat, scoreless name lands in the TRIM band.
    cfg = dataclasses.replace(SMALL_UNIVERSE, sell_threshold=-0.9, trim_threshold=0.9)
    provider = _with_index(FakeProvider(), cfg, choppy())
    provider.set_history("AAA", Market.US, choppy())
    holding = Holding("AAA", Market.US, 10, 100.0)

    reco = _engine(provider, cfg).analyze("AAA", Market.US, holding=holding)

    assert reco is not None and reco.action is Action.TRIM
    assert reco.stop_loss is not None


def test_risk_off_raises_the_bar_for_a_new_entry():
    """The same score that qualifies in a risk-on tape must not qualify in risk-off."""
    provider = FakeProvider()
    provider.set_history("AAA", Market.US, momentum_uptrend())

    calibrated = None
    for cfg_index_bars, expect_buy in ((momentum_uptrend(), True), (momentum_downtrend(), False)):
        cfg = dataclasses.replace(SMALL_UNIVERSE, buy_threshold=0.0, risk_off_buy_penalty=5.0)
        p = _with_index(FakeProvider(), cfg, cfg_index_bars)
        p.set_history("AAA", Market.US, momentum_uptrend())
        reco = _engine(p, cfg).analyze("AAA", Market.US)
        assert (reco is not None) is expect_buy
        calibrated = reco or calibrated
    assert calibrated is not None  # the risk-on case really did produce a BUY


@pytest.mark.xfail(
    strict=True,
    reason="Known defect: the live composite divides by the full weight sum even "
           "when fundamentals/news returned a no-data 0.0, so live scores are "
           "compressed into [-0.70, +0.70] while the thresholds were tuned on a "
           "backtest that renormalises over technical+macro and spans [-1, +1].",
)
def test_missing_fundamentals_and_news_do_not_dilute_the_composite():
    """A neutral 0.0 that means "no data" must not be averaged in as an opinion.

    fundamental_score returns 0.0 when a symbol has no fundamentals, and
    sentiment_score returns 0.0 when there is no news — which for BIST names is
    the permanent state (Finnhub does not cover them). If those weights stay in
    the denominator, 30% of the score is dead weight and the composite can never
    leave [-0.70, +0.70], even though the thresholds were tuned on a backtest
    that renormalises over technical+macro and does span [-1, +1].

    So the composite must renormalise over the sub-scores that actually had data.
    """
    cfg = dataclasses.replace(SMALL_UNIVERSE, sell_threshold=-0.99)
    bars = momentum_downtrend()
    provider = _with_index(FakeProvider(), cfg, bars)
    provider.set_history("AAA", Market.US, bars)
    engine = _engine(provider, cfg)
    holding = Holding("AAA", Market.US, 10, 100.0)

    reco = engine.analyze("AAA", Market.US, holding=holding)
    assert reco is not None

    # With fundamentals and news absent, the composite must equal the
    # technical+macro blend on its own — the same formula the backtest validates.
    tech, _ = technical_score(bars_to_frame(bars), cfg)
    macro = engine.regime(Market.US).score
    expected = (cfg.w_technical * tech.value + cfg.w_macro * macro) / (
        cfg.w_technical + cfg.w_macro
    )
    assert reco.score == pytest.approx(expected, abs=1e-4)


def test_a_flat_series_does_not_crash_the_engine():
    provider = _with_index(FakeProvider(), SMALL_UNIVERSE, flat())
    provider.set_history("AAA", Market.US, flat())
    # Zero ATR, zero momentum: must return something or nothing, never raise.
    _engine(provider).analyze("AAA", Market.US, holding=Holding("AAA", Market.US, 1, 1.0))


# --------------------------------- scan -----------------------------------

def test_scan_never_emits_a_short():
    cfg = dataclasses.replace(SMALL_UNIVERSE, buy_threshold=-1.0)
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    provider.set_history("AAA", Market.US, momentum_uptrend())
    provider.set_history("BBB", Market.BIST, momentum_downtrend())

    recos = _engine(provider, cfg).scan([])

    assert recos  # something was produced
    assert all(r.action in (Action.BUY, Action.SELL, Action.HOLD, Action.TRIM) for r in recos)


def test_scan_does_not_offer_to_buy_something_already_held():
    cfg = dataclasses.replace(SMALL_UNIVERSE, buy_threshold=-1.0)
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    provider.set_history("AAA", Market.US, momentum_uptrend())
    holdings = [Holding("AAA", Market.US, 10, 100.0)]

    recos = _engine(provider, cfg).scan(holdings)

    assert [r.symbol for r in recos].count("AAA") == 1
    assert all(r.action is not Action.BUY for r in recos if r.symbol == "AAA")


def test_scan_skips_symbols_with_no_data_without_raising():
    cfg = dataclasses.replace(SMALL_UNIVERSE, buy_threshold=-1.0)
    provider = _with_index(FakeProvider(), cfg, momentum_uptrend())
    # AAA and BBB have no history at all.
    assert _engine(provider, cfg).scan([]) == []


# -------------------------------- ranking ---------------------------------

def _reco(action: Action, score: float) -> Recommendation:
    return Recommendation(symbol="X", market=Market.US, action=action,
                          score=score, price=10.0, stop_loss=None,
                          take_profit=None, suggested_weight=None)


def test_sells_rank_before_buys_before_trims_before_holds():
    recos = [
        _reco(Action.HOLD, 0.1), _reco(Action.BUY, 0.5),
        _reco(Action.TRIM, -0.2), _reco(Action.SELL, -0.8),
    ]
    ordered = [r.action for r in sorted(recos, key=_rank_key)]
    assert ordered == [Action.SELL, Action.BUY, Action.TRIM, Action.HOLD]


def test_within_buys_the_strongest_conviction_comes_first():
    recos = [_reco(Action.BUY, 0.45), _reco(Action.BUY, 0.9), _reco(Action.BUY, 0.6)]
    assert [r.score for r in sorted(recos, key=_rank_key)] == [0.9, 0.6, 0.45]


def test_within_sells_the_most_urgent_comes_first():
    recos = [_reco(Action.SELL, -0.4), _reco(Action.SELL, -0.95)]
    assert [r.score for r in sorted(recos, key=_rank_key)] == [-0.95, -0.4]
