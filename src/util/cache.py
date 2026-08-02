"""A tiny time-boxed cache, shared by everything that memoises market data.

Anything cached here is a snapshot of a moving market, so it must expire. The
process runs for weeks at a time (the digest fires every morning at 08:30), which
makes an unbounded cache a correctness bug rather than a memory one: a value
computed on the first morning would otherwise still be in use a month later.
"""
from __future__ import annotations

import time


class TTLCache:
    """Maps a key to a value that is forgotten `ttl_seconds` after it was stored.

    Uses a monotonic clock, so a system clock adjustment cannot make an entry
    look arbitrarily old or arbitrarily fresh.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._data: dict[str, tuple[float, object]] = {}

    def get(self, key: str) -> object | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        stored_at, value = hit
        if (time.monotonic() - stored_at) > self._ttl:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._data.clear()
