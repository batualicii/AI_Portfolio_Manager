"""Delivery plumbing: caching, escaping, and message chunking.

Nothing here is strategy logic, but each one silently corrupts the digest when it
is wrong — a stale regime, a rejected message, an over-length chunk.
"""
from __future__ import annotations

import pytest

from src.bot.markdown import escape_md
from src.bot.telegram_bot import _TG_LIMIT, _split_for_telegram
from src.util.cache import TTLCache


# --------------------------------- cache ----------------------------------

def test_a_value_is_returned_within_its_ttl():
    cache = TTLCache(60.0)
    cache.set("k", "v")
    assert cache.get("k") == "v"


def test_a_value_expires_once_the_ttl_has_passed(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("src.util.cache.time.monotonic", lambda: clock["now"])

    cache = TTLCache(60.0)
    cache.set("k", "v")
    clock["now"] += 59.0
    assert cache.get("k") == "v"
    clock["now"] += 2.0
    assert cache.get("k") is None


def test_a_missing_key_is_none():
    assert TTLCache(60.0).get("nope") is None


def test_a_falsy_value_is_still_a_cache_hit():
    cache = TTLCache(60.0)
    cache.set("k", 0)
    assert cache.get("k") == 0


def test_the_regime_is_recomputed_after_the_ttl_expires(monkeypatch):
    """A long-lived bot must not hold the first morning's regime forever.

    The engine is constructed once and the process runs for weeks, so an
    unbounded cache here is a correctness bug: the bot would keep sizing
    positions for a bull market long after the index rolled over.
    """
    import dataclasses

    from src.models import Market
    from src.signals.config import SignalConfig
    from src.signals.engine import SignalEngine
    from tests.conftest import FakeNews, FakeProvider, momentum_downtrend, momentum_uptrend

    clock = {"now": 1000.0}
    monkeypatch.setattr("src.util.cache.time.monotonic", lambda: clock["now"])

    cfg = dataclasses.replace(SignalConfig(), us_universe=(), bist_universe=())
    provider = FakeProvider()
    index = cfg.index_symbol[Market.US]
    provider.set_history(index, Market.US, momentum_uptrend())
    engine = SignalEngine(provider, FakeNews(), cfg, regime_ttl=3600.0)

    assert engine.regime(Market.US).risk_off is False

    # The market rolls over; within the TTL the engine keeps the cached view.
    provider.set_history(index, Market.US, momentum_downtrend())
    assert engine.regime(Market.US).risk_off is False

    clock["now"] += 3601.0
    assert engine.regime(Market.US).risk_off is True


# -------------------------------- escaping ---------------------------------

def test_plain_text_passes_through_untouched():
    assert escape_md("AAPL up 5 percent") == "AAPL up 5 percent"


@pytest.mark.parametrize("char", ["_", "*", "`", "["])
def test_each_active_markdown_character_is_escaped(char):
    assert escape_md(f"a{char}b") == f"a\\{char}b"


def test_a_headline_with_an_underscore_cannot_break_the_message():
    # Telegram rejects the whole message when Markdown does not balance, so an
    # unbalanced underscore in a news headline would delete the entire digest.
    assert escape_md("Q3_results beat") == "Q3\\_results beat"


def test_escaping_handles_empty_and_none():
    assert escape_md("") == ""
    assert escape_md(None) == ""


# -------------------------------- chunking ---------------------------------

def test_a_short_message_is_left_as_one_chunk():
    assert _split_for_telegram("hello") == ["hello"]


def test_a_long_message_is_split_on_line_boundaries():
    text = "\n".join(f"line {i} " + "x" * 100 for i in range(80))
    chunks = _split_for_telegram(text)
    assert len(chunks) > 1
    assert all(len(c) <= _TG_LIMIT for c in chunks)


def test_no_content_is_lost_when_splitting():
    text = "\n".join(f"line {i}" for i in range(2000))
    rejoined = "\n".join(_split_for_telegram(text))
    assert rejoined.split() == text.split()


def test_a_single_over_length_line_is_hard_split():
    """Splitting on line boundaries alone still emits an over-length chunk.

    Telegram rejects any message past its cap, so a very long LLM rationale on
    one line has to be broken mid-line rather than sent whole.
    """
    chunks = _split_for_telegram("y" * (_TG_LIMIT * 3))
    assert len(chunks) >= 3
    assert all(len(c) <= _TG_LIMIT for c in chunks)
    assert "".join(chunks) == "y" * (_TG_LIMIT * 3)


def test_an_over_length_line_mixed_with_normal_ones_stays_within_the_cap():
    text = "short header\n" + "z" * (_TG_LIMIT * 2) + "\nshort footer"
    chunks = _split_for_telegram(text)
    assert all(len(c) <= _TG_LIMIT for c in chunks)
    assert chunks[0].startswith("short header")
    assert chunks[-1].endswith("short footer")


# --------------------------- thesis-era bot surface --------------------------

def test_the_falsifier_syntax_reads_each_supported_form():
    from src.bot.telegram_bot import _parse_falsifier
    from src.models import FalsifierKind

    trend = _parse_falsifier("trend:200/4")
    assert trend.kind is FalsifierKind.TREND_BREAK
    assert (trend.lookback, trend.persistence) == (200, 4)

    dd = _parse_falsifier("drawdown:0.5")
    assert dd.kind is FalsifierKind.DRAWDOWN and dd.threshold == 0.5

    growth = _parse_falsifier("growth:0.20")
    assert growth.kind is FalsifierKind.REVENUE_GROWTH
    assert "under 20%" in growth.text

    ask = _parse_falsifier("ask: a rival ships at scale")
    assert ask.kind is FalsifierKind.MANUAL and not ask.checkable


def test_an_unreadable_falsifier_is_rejected_rather_than_guessed():
    from src.bot.telegram_bot import _parse_falsifier

    assert _parse_falsifier("trend:abc") is None
    assert _parse_falsifier("nonsense") is None
    assert _parse_falsifier("ask:") is None      # a question with no question


def test_the_bot_no_longer_offers_a_scan_command():
    """Ranked buy/sell output is what this design removed, not a missing feature."""
    import inspect

    from src.bot import telegram_bot

    source = inspect.getsource(telegram_bot.PortfolioBot._register)
    assert '"scan"' not in source
    assert '"thesis"' in source and '"close"' in source


@pytest.fixture()
def bot(tmp_path):
    """A real PortfolioBot over fake data — no network, no token validation."""
    from src.bot.telegram_bot import PortfolioBot
    from src.config import Settings
    from src.signals.engine import SignalEngine
    from src.storage.db import Store
    from tests.conftest import FakeNews, FakeProvider

    settings = Settings(
        telegram_bot_token="123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        telegram_owner_id=1, anthropic_api_key=None, anthropic_model="x",
        finnhub_api_key=None, digest_timezone="Europe/Istanbul",
        digest_time="08:30", db_path=tmp_path / "d.db",
    )
    store = Store(tmp_path / "d.db")
    provider = FakeProvider({})
    yield PortfolioBot(settings, store, provider, SignalEngine(provider, FakeNews())), store
    store.close()


def test_the_weekly_summary_contains_no_buy_or_sell_calls(bot):
    """The plan's acceptance criterion for the pivot."""
    portfolio_bot, _ = bot
    text = portfolio_bot.build_digest()
    for word in ("BUY", "SELL", "TRIM"):
        assert word not in text
    assert "Weekly review" in text


def test_the_daily_check_says_nothing_when_nothing_fired(bot):
    """Silence is the correct output almost every day."""
    portfolio_bot, _ = bot
    assert portfolio_bot.check_falsifiers() == []


def test_a_fired_falsifier_produces_one_alert_and_is_logged(bot):
    from datetime import datetime, timezone

    from src.models import Falsifier, FalsifierKind, Market, Thesis

    portfolio_bot, store = bot
    tid = store.open_thesis(Thesis(
        symbol="X", market=Market.US, opened_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        entry_price=100.0, conviction=3, summary="why I own it",
        falsifiers=(Falsifier(FalsifierKind.DRAWDOWN, "halved", threshold=0.5),),
    ))
    # FakeProvider has no history for X, so the check cannot be evaluated and
    # must not fire — an unanswerable question is not a breach.
    assert portfolio_bot.check_falsifiers() == []

    # Now give it a price series that halves from its post-entry high.
    from tests.conftest import _bars
    portfolio_bot._monitor._provider.set_history("X", Market.US,
                                                 _bars([100.0, 200.0, 80.0]))
    messages = portfolio_bot.check_falsifiers()

    assert len(messages) == 1
    assert "conditions you wrote at purchase" in messages[0] or \
           "condition you wrote at purchase" in messages[0]
    assert [e["kind"] for e in store.thesis_events(tid)][0] == "FIRED"


def test_positions_group_by_what_changed_never_by_what_to_do(bot, tmp_path):
    """Grouping is factual. "Sell these" is the rule this design removed."""
    from src.market.types import Fundamentals
    from src.models import Holding, Market
    from tests.conftest import downtrend, uptrend

    portfolio_bot, store = bot
    provider = portfolio_bot._provider
    for symbol, bars in (("UP", uptrend(400)), ("DOWN", downtrend(400))):
        provider.set_history(symbol, Market.US, bars)
        provider._fundamentals[(symbol, Market.US)] = Fundamentals(
            symbol=symbol, sector="Tech", revenue_growth=0.2, profit_margin=0.1,
            pe_ratio=20.0)
        store.upsert_holding(Holding(symbol=symbol, market=Market.US,
                                     quantity=10, avg_cost=100.0))

    cards, groups, summary = portfolio_bot._position_cards(store.list_holdings())

    assert {c.symbol for c in groups["Nothing has broken"]} == {"UP"}
    assert {c.symbol for c in groups["Something changed"]} == {"DOWN"}
    assert "0/2 with a thesis" in summary


def test_positions_are_ordered_by_weight_not_by_alphabet(bot):
    """The biggest exposure is the one worth reading first."""
    from src.market.types import Fundamentals
    from src.models import Holding, Market
    from tests.conftest import uptrend

    portfolio_bot, store = bot
    for symbol, qty in (("AAA", 1), ("ZZZ", 100)):
        portfolio_bot._provider.set_history(symbol, Market.US, uptrend(400))
        portfolio_bot._provider._fundamentals[(symbol, Market.US)] = Fundamentals(
            symbol=symbol, sector="Tech")
        store.upsert_holding(Holding(symbol=symbol, market=Market.US,
                                     quantity=qty, avg_cost=100.0))

    _, groups, _ = portfolio_bot._position_cards(store.list_holdings())
    intact = groups["Nothing has broken"]
    assert [c.symbol for c in intact] == ["ZZZ", "AAA"]


def test_the_pool_is_served_from_storage_rather_than_rescanned(bot):
    """Six hundred names is minutes of calls; a command cannot hold that open."""
    import inspect

    from src.bot import telegram_bot

    source = inspect.getsource(telegram_bot.PortfolioBot._pool)
    assert "build_pool_cards" in source
    # A rescan happens only when the owner explicitly asks for one.
    assert 'if "REFRESH" in args' in source


def test_the_pool_is_scanned_weekly_but_delivered_monthly(bot):
    """A fresh list of twenty names every week manufactures trades."""
    import inspect

    from src.bot import telegram_bot

    source = inspect.getsource(telegram_bot.PortfolioBot._on_startup)
    assert 'name="pool_scan"' in source and 'day_of_week="sat"' in source
    assert 'name="monthly_pool"' in source and "day=1" in source
