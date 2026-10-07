"""Every reply that tells the user what the bot can do is built from API_CATALOG,
so adding an endpoint (e.g. equipment) updates them all at once."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from src.connectbot.handlers import _build_intent_clarification
from src.connectbot.intent_parser import IntentParser
from src.connectbot.orchestrator import ConnectBotOrchestrator
from src.connectbot.response_formatter import API_CATALOG, ResponseFormatter, capability_summary
from tests.conftest import FakeChatLLM


def test_summary_names_every_catalog_entry() -> None:
    summary = capability_summary()
    for name, _ in API_CATALOG:
        assert name.lower() in summary
    assert summary.endswith(f"or {API_CATALOG[-1][0].lower()}")


def test_retired_reply_lists_live_capabilities(orch: ConnectBotOrchestrator) -> None:
    text = orch.process_query("c", "trimmer metrics").text
    assert "trimmers" in text
    assert capability_summary() in text


def test_low_confidence_clarification_lists_capabilities(
    make_orchestrator: Callable[..., ConnectBotOrchestrator], monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = make_orchestrator(FakeChatLLM())
    monkeypatch.setattr(
        orch.intent_parser, "parse", lambda q: {"endpoint": "backlog", "parameters": {}, "confidence": 0.2}
    )
    assert capability_summary() in orch.process_query("c", "how many machines have we made this year").text


def test_unsupported_reply_lists_capabilities(
    make_orchestrator: Callable[..., ConnectBotOrchestrator], monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = make_orchestrator(FakeChatLLM())
    monkeypatch.setattr(orch.intent_parser, "parse", lambda q: {"endpoint": None, "parameters": {}, "confidence": 0.0})
    assert capability_summary() in orch.process_query("c", "how many machines have we made this year").text


def test_unsupported_blocks_list_every_catalog_entry() -> None:
    text = ResponseFormatter.format_unsupported_query()[0].text.text
    for name, desc in API_CATALOG:
        assert f"*{name}* — {desc}" in text


def test_help_text_lists_capabilities() -> None:
    assert capability_summary() in ResponseFormatter.to_text("help")


def test_handler_clarification_lists_capabilities() -> None:
    result = _build_intent_clarification({"endpoint": "backlog", "confidence": 0.1})
    assert result is not None
    assert capability_summary() in result[0]


def test_legacy_parser_thanks_reply_lists_capabilities() -> None:
    parser = IntentParser("", [], "", llm_client=FakeChatLLM())
    intent = parser._fallback_parse("thanks")
    assert capability_summary() in intent["parameters"]["message"]
