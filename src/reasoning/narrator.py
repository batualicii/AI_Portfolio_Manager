"""Claude-powered narrator — explanation-only layer over deterministic signals.

One API call per scan narrates all actionable recommendations at once (cheap, low
latency). Uses structured JSON output so parsing can't fail. If the API errors or no
key is configured, callers fall back to the engine's own deterministic notes.

The quant/LLM wall (SPEC §1) is enforced two ways:
  1. The system prompt forbids inventing any number, price, or prediction.
  2. The model only ever sees facts the Python engine already computed.
"""
from __future__ import annotations

import json
import logging

import anthropic

from src.config import Settings
from src.models import Recommendation

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are a financial advisor explaining pre-computed swing-trading signals to a "
    "retail investor in a daily digest. You will receive a JSON list of signals that a "
    "deterministic quantitative engine already produced.\n\n"
    "Your ONLY job is to write a short, plain-English rationale for each signal, based "
    "strictly on the facts given. Keep each rationale to ONE concise sentence of at most "
    "30 words — it must be complete, never cut off mid-thought.\n\n"
    "HARD RULES:\n"
    "- NEVER invent or state any number, price, percentage, target, or probability that "
    "is not in the provided facts. Refer to the given levels in words if helpful.\n"
    "- NEVER predict where the price will go or promise returns. Describe the setup, not "
    "the future.\n"
    "- Explain WHY the signal fired using the provided technical/fundamental/macro notes.\n"
    "- Be concise, neutral, and direct. No hype, no emojis, no disclaimers.\n"
    "- Output ONLY the requested JSON. No preamble."
)

# Structured output: an array of {symbol, rationale} so parsing is guaranteed valid.
_SCHEMA = {
    "type": "object",
    "properties": {
        "explanations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["symbol", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["explanations"],
    "additionalProperties": False,
}


class ClaudeNarrator:
    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise ValueError("ClaudeNarrator requires an Anthropic API key")
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def narrate(
        self, recos: list[Recommendation]
    ) -> tuple[list[Recommendation], str | None]:
        """Return (recommendations, error).

        On success the recommendations carry LLM prose in `rationale` and the error
        is None. On any failure the input is returned unchanged — the deterministic
        notes are always a usable fallback — and the error string explains why.

        Callers MUST surface that error. A narrator that fails every single call
        looks exactly like a narrator that is switched off, which is how a stale
        SDK pin silently disabled this whole layer once already.

        Only actionable calls are narrated; HOLDs keep their terse notes.
        """
        from src.models import Action

        actionable = [r for r in recos if r.action is not Action.HOLD]
        if not actionable:
            return recos, None

        facts = [
            {
                "symbol": r.symbol,
                "market": r.market.value,
                "action": r.action.value,
                "reference_price": r.price,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "suggested_weight_pct": (
                    round(r.suggested_weight * 100, 1) if r.suggested_weight else None
                ),
                "signal_score": r.score,
                "computed_notes": r.rationale,
            }
            for r in actionable
        ]

        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=4000,  # headroom so the batched JSON never truncates
                system=_SYSTEM,
                output_config={
                    "format": {"type": "json_schema", "schema": _SCHEMA},
                    # Writing one sentence per signal from facts that are already
                    # computed does not need deep reasoning, and thinking is on by
                    # default on current models — low effort keeps the daily digest
                    # cheap and fast without touching output quality.
                    "effort": "low",
                },
                messages=[{
                    "role": "user",
                    "content": (
                        "Write a rationale for each signal. Use only these facts:\n"
                        + json.dumps(facts, indent=2)
                    ),
                }],
            )
        except anthropic.APIStatusError as exc:
            return recos, _fail(f"API error {exc.status_code}: {exc.message}")
        except anthropic.APIConnectionError as exc:
            return recos, _fail(f"could not reach the API: {exc}")

        # Safety classifiers can decline a request with a normal HTTP 200 and an
        # empty content list, so check this before indexing into the response.
        if resp.stop_reason == "refusal":
            return recos, _fail("the model declined to answer")

        try:
            text = next((b.text for b in resp.content if b.type == "text"), "")
            data = json.loads(text)
            by_symbol = {
                e["symbol"].upper(): e["rationale"]
                for e in data.get("explanations", [])
            }
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError) as exc:
            return recos, _fail(f"unreadable response: {exc}")

        out: list[Recommendation] = []
        for r in recos:
            prose = by_symbol.get(r.symbol.upper())
            out.append(_with_rationale(r, prose) if prose else r)
        return out, None


def _fail(reason: str) -> str:
    log.warning("Narration failed (%s); keeping deterministic notes.", reason)
    return reason


def _with_rationale(r: Recommendation, rationale: str) -> Recommendation:
    return Recommendation(
        symbol=r.symbol, market=r.market, action=r.action, score=r.score,
        price=r.price, stop_loss=r.stop_loss, take_profit=r.take_profit,
        suggested_weight=r.suggested_weight, rationale=rationale,
        created_at=r.created_at,
    )
