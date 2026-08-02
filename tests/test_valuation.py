"""Portfolio valuation and its rendering — the arithmetic the user checks daily.

The awkward cases matter most here: a position that cannot be priced, a missing
FX rate, and a zero cost basis. All three must degrade to None rather than
producing a confident wrong number.
"""
from __future__ import annotations

import pytest

from src.models import Holding, Market
from src.portfolio.format import format_report
from src.portfolio.valuation import ValuationService
from tests.conftest import FakeProvider, _bars

AAPL = Holding("AAPL", Market.US, quantity=10, avg_cost=100.0)
THYAO = Holding("THYAO", Market.BIST, quantity=100, avg_cost=200.0)


def _priced_at(price: float):
    return _bars([price - 1.0, price])


@pytest.fixture
def stocked() -> FakeProvider:
    provider = FakeProvider()
    provider.set_history("AAPL", Market.US, _priced_at(150.0))
    provider.set_history("THYAO", Market.BIST, _priced_at(250.0))
    return provider


def test_cost_basis_is_quantity_times_average_cost():
    assert AAPL.cost_basis == pytest.approx(1000.0)


def test_market_value_and_unrealised_pnl(stocked):
    report = ValuationService(stocked).value([AAPL])
    position = report.positions[0]
    assert position.market_value == pytest.approx(1500.0)
    assert position.unrealized_pnl == pytest.approx(500.0)
    assert position.unrealized_pnl_pct == pytest.approx(50.0)


def test_a_loss_is_reported_as_negative(stocked):
    stocked.set_history("AAPL", Market.US, _priced_at(80.0))
    position = ValuationService(stocked).value([AAPL]).positions[0]
    assert position.unrealized_pnl == pytest.approx(-200.0)
    assert position.unrealized_pnl_pct == pytest.approx(-20.0)


def test_an_unpriceable_position_yields_none_rather_than_zero():
    # A missing price must never be silently valued at 0 — that would report a
    # 100% loss on a position that is merely unquoted right now.
    report = ValuationService(FakeProvider()).value([AAPL])
    position = report.positions[0]
    assert position.priced is False
    assert position.price is None
    assert position.market_value is None
    assert position.unrealized_pnl is None
    assert report.unpriced == [position]


def test_a_zero_cost_basis_does_not_divide_by_zero(stocked):
    free_shares = Holding("AAPL", Market.US, quantity=10, avg_cost=0.0)
    position = ValuationService(stocked).value([free_shares]).positions[0]
    assert position.unrealized_pnl_pct is None


def test_subtotals_are_per_market_and_skip_unpriced_positions(stocked):
    report = ValuationService(stocked).value([AAPL, THYAO])
    us_mv, us_cost = report.subtotal(Market.US)
    bist_mv, bist_cost = report.subtotal(Market.BIST)
    assert (us_mv, us_cost) == pytest.approx((1500.0, 1000.0))
    assert (bist_mv, bist_cost) == pytest.approx((25000.0, 20000.0))


def test_grand_totals_convert_through_the_live_fx_rate(stocked):
    report = ValuationService(stocked).value([AAPL, THYAO])
    assert report.usdtry == pytest.approx(35.0)
    # 1500 USD * 35 + 25000 TRY
    assert report.grand_total_try() == pytest.approx(1500 * 35 + 25000)
    # 1500 USD + 25000 TRY / 35
    assert report.grand_total_usd() == pytest.approx(1500 + 25000 / 35)


def test_grand_totals_are_none_without_an_fx_rate():
    provider = FakeProvider(fx={})
    provider.set_history("AAPL", Market.US, _priced_at(150.0))
    report = ValuationService(provider).value([AAPL])
    assert report.usdtry is None
    assert report.grand_total_try() is None
    assert report.grand_total_usd() is None


def test_an_empty_portfolio_values_cleanly():
    report = ValuationService(FakeProvider()).value([])
    assert report.positions == []
    assert report.subtotal(Market.US) == (0.0, 0.0)


# -------------------------------- rendering --------------------------------

def test_report_renders_both_markets_with_totals(stocked):
    text = format_report(ValuationService(stocked).value([AAPL, THYAO]))
    assert "*US*" in text and "*BIST*" in text
    assert "AAPL" in text and "THYAO" in text
    assert "USD/TRY" in text
    assert "*Total*" in text


def test_report_flags_positions_it_could_not_price(stocked):
    ghost = Holding("GHOST", Market.US, quantity=1, avg_cost=1.0)
    text = format_report(ValuationService(stocked).value([AAPL, ghost]))
    assert "Could not price" in text
    assert "GHOST" in text
