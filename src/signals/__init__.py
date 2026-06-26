"""Signal engine — deterministic buy/sell/hold analysis.

CRITICAL (SPEC §1): every number produced here — scores, prices, stop-loss,
take-profit, position size — is computed by this Python code. The LLM never
touches these values; it only narrates them later (Stage 5).
"""
