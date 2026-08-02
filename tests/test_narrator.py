"""The Claude narration layer — explanation only, and never load-bearing.

Two properties matter and both are tested here:

  1. The quant/LLM wall (SPEC section 1): the model receives facts the engine
     already computed and may only replace `rationale`. Every number on the
     Recommendation must survive narration untouched.
  2. Failure is visible. The deterministic notes are a good enough fallback that
     a permanently broken narrator looks identical to a switched-off one — which
     is exactly how a stale SDK pin disabled the layer unnoticed. So `narrate`
     has to report *why* it fell back, not just that it did.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src.bot.telegram_bot import _narration_warning
from src.models import Action, Market, Recommendation
from src.reasoning.narrator import ClaudeNarrator


def _reco(symbol="NVDA", action=Action.BUY, rationale="tech +0.60: computed notes"):
    return Recommendation(
        symbol=symbol, market=Market.US, action=action, score=0.61, price=100.0,
        stop_loss=92.0, take_profit=114.0, suggested_weight=0.08, rationale=rationale,
    )


class _FakeMessages:
    """Stands in for client.messages, recording the request it was given."""

    def __init__(self, *, payload=None, raises=None, stop_reason="end_turn"):
        self._payload = payload
        self._raises = raises
        self._stop_reason = stop_reason
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        if self._raises is not None:
            raise self._raises
        blocks = [SimpleNamespace(type="text", text=self._payload)] if self._payload else []
        return SimpleNamespace(content=blocks, stop_reason=self._stop_reason)


def _narrator(messages) -> ClaudeNarrator:
    settings = SimpleNamespace(anthropic_api_key="test-key", anthropic_model="test-model")
    narrator = ClaudeNarrator.__new__(ClaudeNarrator)
    narrator._client = SimpleNamespace(messages=messages)
    narrator._model = settings.anthropic_model
    return narrator


def _payload(*pairs) -> str:
    return json.dumps({
        "explanations": [{"symbol": s, "rationale": r} for s, r in pairs]
    })


def _status_error(code=500, message="boom"):
    # APIStatusError reaches into response.request, so build a real httpx response.
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(code, request=request)
    return anthropic.APIStatusError(message, response=response, body=None)


# -------------------------------- success ---------------------------------

def test_prose_replaces_the_terse_notes():
    messages = _FakeMessages(payload=_payload(("NVDA", "Trend and momentum both improving.")))
    out, error = _narrator(messages).narrate([_reco()])
    assert error is None
    assert out[0].rationale == "Trend and momentum both improving."


def test_narration_cannot_alter_any_computed_number():
    """The quant/LLM wall: only `rationale` may change."""
    original = _reco()
    messages = _FakeMessages(payload=_payload(("NVDA", "New prose.")))
    (narrated,), _ = _narrator(messages).narrate([original])

    assert narrated.symbol == original.symbol
    assert narrated.market is original.market
    assert narrated.action is original.action
    assert narrated.score == original.score
    assert narrated.price == original.price
    assert narrated.stop_loss == original.stop_loss
    assert narrated.take_profit == original.take_profit
    assert narrated.suggested_weight == original.suggested_weight
    assert narrated.created_at == original.created_at


def test_symbols_are_matched_case_insensitively():
    messages = _FakeMessages(payload=_payload(("nvda", "Lower-case reply.")))
    out, error = _narrator(messages).narrate([_reco()])
    assert error is None and out[0].rationale == "Lower-case reply."


def test_a_symbol_the_model_skipped_keeps_its_computed_notes():
    messages = _FakeMessages(payload=_payload(("NVDA", "Only this one.")))
    out, error = _narrator(messages).narrate([_reco("NVDA"), _reco("AMD")])
    assert error is None
    assert out[0].rationale == "Only this one."
    assert out[1].rationale == "tech +0.60: computed notes"


def test_holds_are_not_sent_to_the_model():
    messages = _FakeMessages(payload=_payload(("NVDA", "Buy prose.")))
    _narrator(messages).narrate([_reco("NVDA"), _reco("AAPL", action=Action.HOLD)])
    sent = json.loads(messages.request["messages"][0]["content"].split("facts:\n")[1])
    assert [f["symbol"] for f in sent] == ["NVDA"]


def test_no_api_call_is_made_when_nothing_is_actionable():
    messages = _FakeMessages(payload=_payload())
    out, error = _narrator(messages).narrate([_reco(action=Action.HOLD)])
    assert messages.request is None  # no tokens spent on a quiet day
    assert error is None and out[0].rationale == "tech +0.60: computed notes"


def test_the_model_only_ever_sees_precomputed_facts():
    messages = _FakeMessages(payload=_payload(("NVDA", "prose")))
    _narrator(messages).narrate([_reco()])
    sent = json.loads(messages.request["messages"][0]["content"].split("facts:\n")[1])
    assert sent[0]["reference_price"] == 100.0
    assert sent[0]["stop_loss"] == 92.0
    assert sent[0]["signal_score"] == 0.61


def test_the_request_pins_the_json_schema_so_parsing_cannot_drift():
    messages = _FakeMessages(payload=_payload(("NVDA", "prose")))
    _narrator(messages).narrate([_reco()])
    fmt = messages.request["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["required"] == ["explanations"]


def test_the_system_prompt_forbids_inventing_numbers():
    messages = _FakeMessages(payload=_payload(("NVDA", "prose")))
    _narrator(messages).narrate([_reco()])
    system = messages.request["system"]
    assert "NEVER invent" in system
    assert "NEVER predict" in system


# -------------------------------- failure ---------------------------------

def test_an_api_error_falls_back_and_says_why():
    messages = _FakeMessages(raises=_status_error(500, "server exploded"))
    out, error = _narrator(messages).narrate([_reco()])
    assert out[0].rationale == "tech +0.60: computed notes"
    assert error is not None and "500" in error


def test_a_connection_error_falls_back_and_says_why():
    messages = _FakeMessages(raises=anthropic.APIConnectionError(request=None))
    out, error = _narrator(messages).narrate([_reco()])
    assert out[0].rationale == "tech +0.60: computed notes"
    assert error is not None and "reach" in error


def test_a_refusal_is_detected_before_the_response_is_indexed():
    # A declined request returns HTTP 200 with an empty content list, so code
    # that reads content[0] straight away would raise instead of falling back.
    messages = _FakeMessages(payload=None, stop_reason="refusal")
    out, error = _narrator(messages).narrate([_reco()])
    assert out[0].rationale == "tech +0.60: computed notes"
    assert error is not None and "declined" in error


def test_malformed_json_falls_back_and_says_why():
    messages = _FakeMessages(payload="not json at all")
    out, error = _narrator(messages).narrate([_reco()])
    assert out[0].rationale == "tech +0.60: computed notes"
    assert error is not None and "unreadable" in error


def test_a_response_with_no_text_block_falls_back():
    messages = _FakeMessages(payload=None)
    out, error = _narrator(messages).narrate([_reco()])
    assert out[0].rationale == "tech +0.60: computed notes"
    assert error is not None


def test_a_schema_shaped_but_wrong_payload_falls_back():
    messages = _FakeMessages(payload=json.dumps({"explanations": [{"symbol": "NVDA"}]}))
    out, error = _narrator(messages).narrate([_reco()])
    assert out[0].rationale == "tech +0.60: computed notes"
    assert error is not None


def test_construction_requires_an_api_key():
    settings = SimpleNamespace(anthropic_api_key=None, anthropic_model="m")
    with pytest.raises(ValueError):
        ClaudeNarrator(settings)


# ------------------------- surfacing the failure --------------------------

def test_a_healthy_narrator_adds_no_warning_to_the_digest():
    assert _narration_warning(None) == ""


def test_a_broken_narrator_is_announced_in_the_digest():
    warning = _narration_warning("API error 500: server exploded")
    assert "Explanation layer unavailable" in warning
    assert "server exploded" in warning
