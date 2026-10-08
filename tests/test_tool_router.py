"""run_tool_loop: the LLM picks endpoints; only offered tools ever execute."""

from __future__ import annotations

from typing import Any

import pytest

from src.connectbot.tool_router import TOOL_DEFS, run_tool_loop
from tests.conftest import FakeLLM, tool_call

BACKLOG_ONLY = [t for t in TOOL_DEFS if t["function"]["name"] == "backlog"]


class Recorder:
    def __init__(self, data: dict[str, Any] | None = {"ok": True}):  # noqa: B006 - never mutated
        self.data = data
        self.intents: list[dict[str, Any]] = []

    def __call__(self, intent: dict[str, Any]) -> dict[str, Any] | None:
        self.intents.append(intent)
        return self.data


def _run(llm: FakeLLM, call_api: Recorder, **kwargs: Any) -> tuple[str, str | None, list[dict[str, Any]]]:
    return run_tool_loop(llm, call_api, lambda _name, data: data, "system", [], "question", **kwargs)


def test_direct_answer_needs_no_tools() -> None:
    answer, model, used = _run(FakeLLM(tool_replies=[{"content": " Hi there "}]), Recorder())
    assert (answer, model, used) == ("Hi there", "fake-tool-model", [])


def test_tool_results_are_fed_back_before_the_answer() -> None:
    llm = FakeLLM(
        tool_replies=[
            {"content": "", "tool_calls": [tool_call("backlog", {})]},
            {"content": "5 lots open."},
        ]
    )
    api = Recorder({"active_lots": 5})

    answer, _, used = _run(llm, api)

    assert answer == "5 lots open."
    assert api.intents == [{"endpoint": "backlog", "parameters": {}}]
    assert used == [{"tool": "backlog", "arguments": {}, "ok": True}]
    tool_message = llm.tool_calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert '"active_lots": 5' in tool_message["content"]


def test_tool_not_offered_is_never_executed() -> None:
    llm = FakeLLM(
        tool_replies=[
            {"content": "", "tool_calls": [tool_call("sales", {})]},
            {"content": "Sales are restricted."},
        ]
    )
    api = Recorder()

    _, _, used = _run(llm, api, tools=BACKLOG_ONLY)

    assert api.intents == []
    assert used == [{"tool": "sales", "arguments": {}, "ok": False}]


def test_bad_json_arguments_become_empty_dict() -> None:
    llm = FakeLLM(
        tool_replies=[
            {"content": "", "tool_calls": [tool_call("backlog", "{not json")]},
            {"content": "done"},
        ]
    )
    api = Recorder()
    _run(llm, api)
    assert api.intents == [{"endpoint": "backlog", "parameters": {}}]


def test_last_round_forces_text_and_cap_raises() -> None:
    looping = {"content": "", "tool_calls": [tool_call("backlog", {})]}
    llm = FakeLLM(tool_replies=[looping, looping])

    with pytest.raises(RuntimeError, match="round cap"):
        _run(llm, Recorder(), max_rounds=2)

    assert [c["tool_choice"] for c in llm.tool_calls] == [None, "none"]


def test_empty_reply_raises() -> None:
    with pytest.raises(RuntimeError, match="neither content nor tool calls"):
        _run(FakeLLM(tool_replies=[{"content": "  "}]), Recorder())


def test_evidence_collects_each_successful_tool_result_by_name() -> None:
    llm = FakeLLM(
        tool_replies=[
            {
                "content": "",
                "tool_calls": [
                    tool_call("heading", {"head_name": "National 1"}, "c1"),
                    tool_call("heading", {"head_name": "National 2"}, "c2"),
                    tool_call("backlog", {}, "c3"),
                ],
            },
            {"content": "done"},
        ]
    )
    evidence: dict[str, Any] = {}

    _run(llm, Recorder({"rows": []}), evidence=evidence)

    assert list(evidence) == ["heading", "heading#2", "backlog"]


def test_evidence_skips_tools_that_returned_nothing() -> None:
    llm = FakeLLM(tool_replies=[{"content": "", "tool_calls": [tool_call("backlog", {})]}, {"content": "none"}])
    evidence: dict[str, Any] = {}
    _run(llm, Recorder(None), evidence=evidence)
    assert evidence == {}


def test_every_requested_call_gets_a_tool_response() -> None:
    # The API 400s ("No tool output found for function call ...") if any call id
    # in the assistant message goes unanswered, so calls past the cap are
    # answered with a "skipped" result rather than dropped.
    calls = [tool_call("heading", {"head_name": f"H{i}"}, f"c{i}") for i in range(10)]
    llm = FakeLLM(tool_replies=[{"content": "", "tool_calls": calls}, {"content": "done"}])
    api = Recorder({"rows": []})

    _run(llm, api)

    answered = [m["tool_call_id"] for m in llm.tool_calls[1]["messages"] if m["role"] == "tool"]
    assert answered == [f"c{i}" for i in range(10)]
    assert len(api.intents) == 8
    skipped = [m for m in llm.tool_calls[1]["messages"] if m["role"] == "tool" and "skipped" in m["content"]]
    assert len(skipped) == 2
