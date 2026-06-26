"""Backtesting — the validation gate (SPEC §6b).

Walks the SAME deterministic signal logic forward through history with no lookahead,
simulates swing trades (stops/targets/costs), and compares the result to a simple
buy-and-hold benchmark. If the strategy can't beat buy-and-hold, the logic changes
before any real money is risked.

NOTE: backtests run on price/trend data only — free historical fundamentals/news
don't exist, so the fundamental & sentiment sub-scores are neutral here. This tests
the technical + macro backbone, which is what swing trading actually leans on.
"""
