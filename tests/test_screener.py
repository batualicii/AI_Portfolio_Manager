"""The screener's thresholds and the reasoning behind them."""
from __future__ import annotations

from src.models import Market
from scripts.screen_candidates import LIMITS


def test_bist_demands_more_liquidity_relative_to_size():
    """Wider spreads there; a position you cannot exit is not a position."""
    us, bist = LIMITS[Market.US], LIMITS[Market.BIST]
    us_ratio = us.min_daily_value_usd / us.max_cap_usd
    bist_ratio = bist.min_daily_value_usd / bist.max_cap_usd
    assert bist_ratio > us_ratio


def test_the_size_band_excludes_names_large_funds_can_own():
    """The whole premise is capacity — a $50B company is not the pond."""
    for market, limits in LIMITS.items():
        assert limits.max_cap_usd <= 5e9, market
        assert limits.min_cap_usd > 0


def test_every_market_caps_institutional_ownership():
    """A name funds already crowded into offers no capacity advantage."""
    for limits in LIMITS.values():
        assert 0 < limits.max_institutional < 1.0


def test_fundamentals_carries_institutional_ownership():
    from src.market.types import Fundamentals

    assert Fundamentals(symbol="X").held_pct_institutions is None
    assert Fundamentals(symbol="X", held_pct_institutions=0.4) \
        .held_pct_institutions == 0.4
