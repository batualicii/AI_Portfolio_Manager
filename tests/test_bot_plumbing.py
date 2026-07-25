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
