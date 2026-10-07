"""run_tool_loop: the LLM picks endpoints; only offered tools ever execute."""

from __future__ import annotations

from typing import Any

import pytest

from src.connectbot.tool_router import TOOL_DEFS, run_tool_loop
from tests.conftest import FakeLLM, tool_call

BACKLOG_ONLY = [t for t in TOOL_DEFS if t["function"]["name"] == "backlog"]


class Recorder:
    def __init__(self, data: dict[str, Any] | None = None):
        self.data = data if data is not None else {"ok": True}
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


def test_at_most_four_tool_calls_run_per_round() -> None:
    calls = [tool_call("backlog", {}, call_id=f"c{i}") for i in range(6)]
    llm = FakeLLM(tool_replies=[{"content": "", "tool_calls": calls}, {"content": "done"}])
    api = Recorder()
    _run(llm, api)
    assert len(api.intents) == 4


def test_last_round_forces_text_and_cap_raises() -> None:
    looping = {"content": "", "tool_calls": [tool_call("backlog", {})]}
    llm = FakeLLM(tool_replies=[looping, looping])

    with pytest.raises(RuntimeError, match="round cap"):
        _run(llm, Recorder(), max_rounds=2)

    assert [c["tool_choice"] for c in llm.tool_calls] == [None, "none"]


def test_empty_reply_raises() -> None:
    with pytest.raises(RuntimeError, match="neither content nor tool calls"):
        _run(FakeLLM(tool_replies=[{"content": "  "}]), Recorder())
