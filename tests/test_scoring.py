"""Sub-score behaviour: bounds, direction, and the documented thresholds.

Every sub-score is contractually in [-1, 1] and carries human-readable notes that
the narrator later turns into prose. Both halves of that contract are tested here.
"""
from __future__ import annotations

import pytest

from src.market.types import Fundamentals, NewsItem
from src.signals import indicators as ind
from src.signals.config import SignalConfig
from src.signals.scoring import (
    fundamental_score,
    sentiment_score,
    technical_score,
)
from tests.conftest import (
    choppy,
    downtrend,
    flat,
    momentum_downtrend,
    momentum_uptrend,
    uptrend,
)

CFG = SignalConfig()


def _tech(bars):
    return technical_score(ind.bars_to_frame(bars), CFG)


# ------------------------------- technical --------------------------------

def test_technical_score_is_positive_on_a_trending_name():
    score, view = _tech(momentum_uptrend())
    assert score.value > 0.25
    assert view.price > 0 and view.atr > 0
    assert view.sma50 is not None and view.sma200 is not None
    assert view.sma50 > view.sma200          # golden cross
    assert any("SMA50" in note for note in score.notes)


def test_technical_score_is_negative_on_a_trending_decline():
    score, _ = _tech(momentum_downtrend())
    assert score.value < -0.2


def test_scores_are_ordered_uptrend_above_choppy_above_downtrend():
    up, _ = _tech(momentum_uptrend())
    side, _ = _tech(choppy())
    down, _ = _tech(momentum_downtrend())
    assert up.value > side.value > down.value


def test_mean_reversion_caps_the_score_of_an_extended_trend():
    """A textbook uptrend does NOT score anywhere near +1.

    Mean reversion reads RSI >= 70 as exhaustion and contributes its full -1.0
    against trend and momentum, so even a name with a perfect golden cross and a
    saturated 20-day ROC tops out around +0.3 on the technical sub-score. That is
    the intended trade-off (the engine will not chase a vertical chart), but it
    is worth pinning: it explains why /scan is often quiet in a strong bull
    market, and any future re-weighting that breaks this should be deliberate.
    """
    score, view = _tech(momentum_uptrend())
    assert view.rsi >= 70
    assert any("overbought" in note for note in score.notes)
    assert score.value < 0.5


def test_technical_score_stays_within_bounds_on_choppy_data():
    score, _ = _tech(choppy())
    assert -1.0 <= score.value <= 1.0


def test_technical_score_survives_a_flat_series_with_zero_atr():
    # Zero ATR is a real divide-by-zero risk in the momentum term.
    score, view = _tech(flat())
    assert view.atr == pytest.approx(0.0)
    assert -1.0 <= score.value <= 1.0


def test_oversold_rsi_is_reported_and_lifts_the_mean_reversion_term():
    score, view = _tech(downtrend())
    assert view.rsi <= 30
    assert any("oversold" in note for note in score.notes)


def test_overbought_rsi_is_reported():
    score, view = _tech(uptrend())
    assert view.rsi >= 70
    assert any("overbought" in note for note in score.notes)


# ------------------------------ fundamental -------------------------------

def test_fundamentals_with_no_data_are_neutral_and_say_so():
    score = fundamental_score(Fundamentals(symbol="X"), CFG)
    assert score.value == 0.0
    assert score.notes == ["limited fundamentals"]


@pytest.mark.parametrize("pe,expected_note", [
    (-5.0, "negative earnings"),
    (10.0, "cheap"),
    (20.0, "fair"),
    (40.0, "rich"),
    (80.0, "very rich"),
])
def test_pe_bands_are_labelled_at_their_documented_boundaries(pe, expected_note):
    score = fundamental_score(Fundamentals(symbol="X", pe_ratio=pe), CFG)
    assert any(expected_note in note for note in score.notes)


def test_cheap_pe_scores_above_expensive_pe():
    cheap = fundamental_score(Fundamentals(symbol="X", pe_ratio=10.0), CFG)
    rich = fundamental_score(Fundamentals(symbol="X", pe_ratio=80.0), CFG)
    assert cheap.value > rich.value


def test_growth_and_margin_saturate_rather_than_exceeding_the_bound():
    # revenue_growth/profit_margin arrive as fractions; 25% saturates the term.
    score = fundamental_score(
        Fundamentals(symbol="X", revenue_growth=5.0, profit_margin=5.0), CFG
    )
    assert score.value == pytest.approx(1.0)


def test_fundamental_score_stays_within_bounds_at_the_negative_extreme():
    score = fundamental_score(
        Fundamentals(symbol="X", pe_ratio=-1.0, revenue_growth=-5.0,
                     profit_margin=-5.0), CFG
    )
    assert -1.0 <= score.value < 0


# -------------------------------- sentiment --------------------------------

def _news(*headlines: str) -> list[NewsItem]:
    from datetime import datetime, timezone
    return [
        NewsItem(symbol="X", headline=h, url="", source="t",
                 published_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        for h in headlines
    ]


def test_no_news_is_neutral():
    score = sentiment_score([], CFG)
    assert score.value == 0.0
    assert score.notes == ["no recent news"]


def test_headlines_without_lexicon_hits_are_neutral_but_counted():
    score = sentiment_score(_news("Company files routine quarterly paperwork"), CFG)
    assert score.value == 0.0
    assert "1 headlines" in score.notes[0]


def test_positive_headlines_score_positive_and_negative_score_negative():
    assert sentiment_score(_news("Firm beats estimates, shares rally"), CFG).value > 0
    assert sentiment_score(_news("Firm misses estimates, shares plunge"), CFG).value < 0


def test_sentiment_is_clamped_so_it_can_nudge_but_never_dominate():
    # Ten unambiguously positive headlines still cannot exceed +0.5.
    score = sentiment_score(_news(*(["beats surge record upgrade"] * 10)), CFG)
    assert score.value == pytest.approx(0.5)


def test_sentiment_is_clamped_on_the_negative_side_too():
    score = sentiment_score(_news(*(["misses plunge downgrade fraud"] * 10)), CFG)
    assert score.value == pytest.approx(-0.5)


def test_mixed_headlines_partially_cancel():
    score = sentiment_score(_news("beats rally", "misses plunge"), CFG)
    assert score.value == pytest.approx(0.0)
