"""Reasoning layer — turns deterministic signals into plain-language explanations.

CRITICAL (SPEC §1): the LLM here is EXPLANATION-ONLY. It receives facts already
computed by the signal engine and writes prose about them. It never produces prices,
scores, levels, or predictions — those all come from Python. The system prompt and a
structured-output schema enforce this.
"""
