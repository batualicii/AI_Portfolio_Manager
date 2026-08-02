"""The stored pool: what it keeps, and what it can therefore say."""
from __future__ import annotations

import datetime as dt

import pytest

from src.models import Market
from src.screen.candidates import Candidate, screen
from src.storage.db import Store
from tests.conftest import FakeProvider, downtrend, uptrend


def _candidate(symbol: str, momentum: float = 0.4, sector: str = "Tech") -> Candidate:
    return Candidate(symbol, sector, 1e9, 2e6, 0.4, momentum, True,
                     0.12, 44.0, 0.19, 0.03, 22.0, 1.4, -0.3)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "pool.db")


def test_the_pool_can_say_which_names_are_new(store):
    """Without that, twenty familiar names read as twenty fresh ideas."""
    store.save_pool(Market.US, [_candidate("AAA"), _candidate("BBB")],
                    dt.datetime(2026, 1, 1))
    store.save_pool(Market.US, [_candidate("BBB"), _candidate("CCC")],
                    dt.datetime(2026, 2, 1))

    assert [r["symbol"] for r in store.pool(Market.US)] == ["BBB", "CCC"]
    assert store.new_in_pool(Market.US) == {"CCC"}


def test_a_first_run_claims_nothing_is_new(store):
    """With no previous screen, everything is new — and saying so is noise."""
    store.save_pool(Market.US, [_candidate("AAA")])
    assert store.new_in_pool(Market.US) == set()


def test_pruning_never_destroys_the_comparison(store):
    """keep_runs=1 would make "new since last time" permanently unanswerable."""
    for month in range(1, 6):
        store.save_pool(Market.US, [_candidate(f"S{month}")],
                        dt.datetime(2026, month, 1), keep_runs=1)
    assert len(store.pool_runs(Market.US, limit=10)) == 2
    assert store.new_in_pool(Market.US) == {"S5"}


def test_markets_do_not_overwrite_each_other(store):
    store.save_pool(Market.US, [_candidate("AAA")])
    store.save_pool(Market.BIST, [_candidate("THYAO")])
    assert [r["symbol"] for r in store.pool(Market.US)] == ["AAA"]
    assert [r["symbol"] for r in store.pool(Market.BIST)] == ["THYAO"]


def test_an_empty_pool_is_empty_rather_than_an_error(store):
    assert store.pool(Market.US) == []
    assert store.pool_runs(Market.US) == []


# ------------------------------- the screen -------------------------------

def _fundamentals(cap: float, held: float | None = 0.4):
    from src.market.types import Fundamentals
    return Fundamentals(symbol="X", market_cap=cap, held_pct_institutions=held,
                        sector="Tech", revenue_growth=0.2, profit_margin=0.1,
                        pe_ratio=20.0, beta=1.1)


def test_the_screen_drops_names_the_filters_reject_and_says_which_filter():
    provider = FakeProvider(
        history={("BIG", Market.US): uptrend(300),
                 ("OK", Market.US): uptrend(300),
                 ("THIN", Market.US): uptrend(300)},
        fundamentals={("BIG", Market.US): _fundamentals(50e9),
                      ("OK", Market.US): _fundamentals(1e9),
                      ("THIN", Market.US): _fundamentals(1e9)},
    )
    # THIN trades a fraction of the floor: same price series, tiny volume.
    provider.set_history("THIN", Market.US, [
        b.__class__(**{**b.__dict__, "volume": 1.0})
        for b in provider.get_history("THIN", Market.US)
    ])

    result = screen(provider, Market.US, ["BIG", "OK", "THIN"], {}, fx=1.0)

    assert [c.symbol for c in result.kept] == ["OK"]
    assert result.dropped["size"] == 1
    assert result.dropped["liquidity"] == 1


def test_the_screen_carries_the_numbers_it_filtered_on():
    """Refetching later would display a different moment than the one screened."""
    provider = FakeProvider(
        history={("OK", Market.US): uptrend(300)},
        fundamentals={("OK", Market.US): _fundamentals(1e9)},
    )
    kept = screen(provider, Market.US, ["OK"], {}, fx=1.0).kept[0]

    assert kept.revenue_growth == 0.2
    assert kept.pe_ratio == 20.0
    assert kept.vs_200d is not None
    assert kept.worst_drawdown is not None


def test_the_reading_list_caps_one_sector_from_filling_it():
    """The ownership filter favours small banks; uncapped, that fills the list."""
    from src.screen.candidates import ScreenResult

    kept = [_candidate(f"B{i}", momentum=1.0 - i / 100, sector="Financials")
            for i in range(10)]
    kept.append(_candidate("TECH", momentum=0.0, sector="Tech"))
    result = ScreenResult(Market.US, kept, {}, {}, 11)

    reading = result.reading_list(top=5, max_per_sector=3)
    assert [c.symbol for c in reading] == ["B0", "B1", "B2", "TECH"]


def test_a_name_the_provider_cannot_answer_for_is_counted_not_fatal():
    class Broken(FakeProvider):
        def get_fundamentals(self, symbol, market):
            raise RuntimeError("upstream is down")

    result = screen(Broken(history={("X", Market.US): downtrend(300)}),
                    Market.US, ["X"], {}, fx=1.0)
    assert result.kept == []
    assert result.dropped["no data"] == 1
