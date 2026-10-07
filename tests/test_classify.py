"""general vs business classification, cheapest path first."""

from __future__ import annotations

from collections.abc import Callable

from src.connectbot.orchestrator import ConnectBotOrchestrator
from tests.conftest import FakeLLM


def test_connectivity_question_is_general_without_llm(make_orchestrator: Callable[..., ConnectBotOrchestrator]) -> None:
    llm = FakeLLM()
    orch = make_orchestrator(llm)
    assert orch._classify_query("c", "are you connected to fastapi?") == ("general", None)
    assert llm.calls == []


def test_greeting_is_general_without_llm(make_orchestrator: Callable[..., ConnectBotOrchestrator]) -> None:
    llm = FakeLLM()
    orch = make_orchestrator(llm)
    assert orch._classify_query("c", "hi!") == ("general", None)
    assert llm.calls == []


def test_business_keyword_is_business_without_llm(make_orchestrator: Callable[..., ConnectBotOrchestrator]) -> None:
    llm = FakeLLM()
    orch = make_orchestrator(llm)
    assert orch._classify_query("c", "can you tell me our backlog") == ("business", None)
    assert llm.calls == []


def test_short_followup_keeps_previous_mode(orch: ConnectBotOrchestrator) -> None:
    orch._last_mode["c"] = "business"
    assert orch._classify_query("c", "and yesterday?") == ("business", None)


def test_unknown_question_asks_llm_classifier(make_orchestrator: Callable[..., ConnectBotOrchestrator]) -> None:
    llm = FakeLLM(replies=['```json\n{"mode": "business", "reasoning": "ops"}\n```'])
    orch = make_orchestrator(llm)
    assert orch._classify_query("c", "whats the best black ops 2 map of all time") == ("business", "fake-model")


def test_unparseable_classifier_reply_falls_back_to_general(
    make_orchestrator: Callable[..., ConnectBotOrchestrator],
) -> None:
    orch = make_orchestrator(FakeLLM(replies=["not json"]))
    assert orch._classify_query("c", "whats the best black ops 2 map of all time") == ("general", None)
