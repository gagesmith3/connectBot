"""End-to-end orchestrator paths through process_query, with fake LLM and fake FastAPI."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from src.connectbot.orchestrator import ConnectBotOrchestrator
from src.connectbot.response_formatter import API_CATALOG
from tests.conftest import FakeChatLLM, FakeFastAPIServer, FakeLLM, tool_call

MakeOrch = Callable[..., ConnectBotOrchestrator]

SALES_ROUTES = {
    "/v1/metrics/sage/daily": {"data_date": "2026-10-06", "sales_total": 39800, "sales_orders": 12},
    "/v1/metrics/sage/summary": {"data_date": "2026-10-06", "sales_trend_pct": 12},
}
HEADING_OVERALL = {"data_date": "2026-10-07", "plan_studs": 300000, "actual_studs": 210000, "volume_pct": 70}


# --- sales access gate -------------------------------------------------------


def test_sales_blocked_for_user_not_on_allowlist(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes.update(SALES_ROUTES)
    orch = make_orchestrator(FakeLLM(), sales_access_users={"U_ALLOWED"})

    result = orch.process_query("c", "sales yesterday", user_id="U_OTHER")

    assert result.mode == "business-restricted"
    assert "restricted" in result.text
    assert fake_api.requests == []


def test_sales_allowed_for_user_on_allowlist(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes.update(SALES_ROUTES)
    orch = make_orchestrator(FakeLLM(replies=["Sales were $39,800 yesterday."]), sales_access_users={"U_ALLOWED"})

    result = orch.process_query("c", "sales yesterday", user_id="U_ALLOWED")

    assert result.mode == "business-grounded"
    assert result.endpoint == "sales"
    assert result.text == "Sales were $39,800 yesterday."
    # daily + summary, then the trailing-average comparison series
    assert fake_api.paths == ["/v1/metrics/sage/daily", "/v1/metrics/sage/summary", "/v1/metrics/sage/trend"]
    assert all(r.url.params["data_date"] == "2026-10-06" for r in fake_api.requests[:2])


def test_empty_allowlist_means_everyone_sees_sales(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes.update(SALES_ROUTES)
    orch = make_orchestrator(FakeLLM(replies=["ok"]))
    assert orch.process_query("c", "sales today", user_id="ANYONE").mode == "business-grounded"


def test_tool_router_hides_sales_tools_from_restricted_user(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    llm = FakeLLM(tool_replies=[{"content": "Heading is at 70%."}])
    orch = make_orchestrator(llm, sales_access_users={"U_ALLOWED"})

    orch.process_query("c", "give me the morning rundown", user_id="U_OTHER")

    offered = llm.tool_calls[0]["tools"]
    assert "sales" not in offered
    assert "sage_trend" not in offered
    assert "heading_overall" in offered


# --- tool router -------------------------------------------------------------


def test_rundown_goes_to_tool_router_with_todays_date(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/heading/overall"] = HEADING_OVERALL
    llm = FakeLLM(
        tool_replies=[
            {"content": "", "tool_calls": [tool_call("heading_overall", {})]},
            {"content": "We're at 70% of plan."},
        ]
    )
    orch = make_orchestrator(llm)

    result = orch.process_query("c", "give me the morning rundown")

    assert result.mode == "business-tool-router"
    assert result.endpoint == "heading_overall"
    assert result.text == "We're at 70% of plan."
    # the tool call, then its prior-workday comparison
    assert fake_api.paths == ["/v1/metrics/heading/overall", "/v1/metrics/heading/overall"]
    assert "Today's date is 2026-10-07" in llm.tool_calls[0]["messages"][0]["content"]


def test_multi_topic_question_skips_keyword_rules(make_orchestrator: MakeOrch) -> None:
    llm = FakeLLM(tool_replies=[{"content": "Both look fine."}])
    orch = make_orchestrator(llm)
    result = orch.process_query("c", "how are backlog and heading looking")
    assert result.mode == "business-tool-router"


def test_tool_router_failure_falls_back_to_legacy_parser(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_api.routes["/v1/metrics/backlog"] = {"active_lots": 5, "total_qty": 1000}
    llm = FakeLLM(replies=["Backlog is 5 lots."], tool_replies=[RuntimeError("router down")])
    orch = make_orchestrator(llm)
    parsed: list[str] = []

    def fake_parse(query: str) -> dict[str, Any]:
        parsed.append(query)
        return {"endpoint": "backlog", "parameters": {}, "confidence": 0.9}

    monkeypatch.setattr(orch.intent_parser, "parse", fake_parse)

    result = orch.process_query("c", "how many machines have we made this year")

    assert parsed == ["how many machines have we made this year"]
    assert result.mode == "business-grounded"
    assert result.endpoint == "backlog"


# --- deterministic replies ---------------------------------------------------


def test_retired_topic_answers_honestly_without_api_call(
    orch: ConnectBotOrchestrator, fake_api: FakeFastAPIServer
) -> None:
    result = orch.process_query("c", "trimmer metrics")
    assert result.mode == "business-retired"
    assert "trimmers" in result.text
    assert fake_api.requests == []


def test_low_confidence_intent_asks_for_clarification(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = make_orchestrator(FakeChatLLM())  # no tool calling -> legacy parser path
    monkeypatch.setattr(
        orch.intent_parser, "parse", lambda q: {"endpoint": "backlog", "parameters": {}, "confidence": 0.2}
    )
    result = orch.process_query("c", "how many machines have we made this year")
    assert result.mode == "business-clarification"
    assert fake_api.requests == []


def test_unmapped_business_question_is_unsupported(
    make_orchestrator: MakeOrch, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = make_orchestrator(FakeChatLLM())
    monkeypatch.setattr(orch.intent_parser, "parse", lambda q: {"endpoint": None, "parameters": {}, "confidence": 0.0})
    assert orch.process_query("c", "how many machines have we made this year").mode == "business-unsupported"


def test_capabilities_reply_lists_the_catalog_without_llm(make_orchestrator: MakeOrch) -> None:
    llm = FakeLLM()
    result = make_orchestrator(llm).process_query("c", "hi, what can you do?")
    assert result.mode == "general-social"
    for name, _ in API_CATALOG:
        assert name in result.text
    assert llm.calls == []


def test_fastapi_health_question_reports_failing_jobs(
    orch: ConnectBotOrchestrator, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes["/health"] = {"db_connected": True, "jobs": [{"job_name": "heading_daily", "status": "failed"}]}
    result = orch.process_query("c", "are you connected to fastapi?")
    assert result.mode == "general-fastapi-check"
    assert "connected" in result.text
    assert "heading_daily" in result.text


# --- failures ----------------------------------------------------------------


def test_api_outage_says_data_is_unavailable(orch: ConnectBotOrchestrator) -> None:
    result = orch.process_query("c", "can you tell me our backlog")
    assert result.mode == "business-unavailable"
    assert result.endpoint == "backlog"


def test_llm_failure_falls_back_to_structured_text(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/backlog"] = {"active_lots": 5, "total_qty": 1000}
    orch = make_orchestrator(FakeLLM(replies=[RuntimeError("llm down")]))

    result = orch.process_query("c", "can you tell me our backlog")

    assert result.mode == "business-grounded"
    assert result.model is None
    assert result.text.startswith("Current backlog status")


def test_local_fail_closed_reports_local_ai_down(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/backlog"] = {"active_lots": 5}
    orch = make_orchestrator(
        FakeChatLLM(replies=[RuntimeError("ollama down")]), llm_provider="ollama", local_only_fail_closed=True
    )
    assert orch.process_query("c", "can you tell me our backlog").mode == "business-local-unavailable"


# --- follow-ups and deterministic livewire -----------------------------------


def test_followup_reuses_endpoint_with_new_date(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/heading/overall"] = HEADING_OVERALL
    orch = make_orchestrator(FakeLLM(replies=["70% today.", "65% yesterday."]))

    orch.process_query("c", "hows heading going today")
    result = orch.process_query("c", "and yesterday?")

    assert result.endpoint == "heading_overall"
    assert fake_api.requests[-1].url.path == "/v1/metrics/heading/overall"
    assert fake_api.requests[-1].url.params["data_date"] == "2026-10-06"


def test_livewire_demand_is_formatted_without_llm(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/livewire/demand"] = {
        "count": 1,
        "rows": [
            {"material_code": "1010-MS", "material_name": "Mild Steel", "delta_lbs": -250.0, "shortage_flag": True}
        ],
    }
    llm = FakeLLM()
    result = make_orchestrator(llm).process_query("c", "how much mild steel should i order?")

    assert result.mode == "business-grounded"
    assert result.model is None
    assert "1 shortage row" in result.text
    assert llm.calls == []


def test_empty_livewire_filter_says_nothing_matched(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/livewire/demand"] = {
        "count": 1,
        "rows": [{"material_code": "302", "material_name": "302 SS"}],
    }
    llm = FakeLLM()
    result = make_orchestrator(llm).process_query("c", "how much mild steel should i order?")
    assert "No livewire demand rows matched 'mild steel'" in result.text
    assert llm.calls == []


# --- structured answers (answer spec) -----------------------------------------

BACKLOG = {"active_lots": 5, "total_qty": 1000, "snapshot_ts": "2026-10-07T06:00:00"}
BACKLOG_SPEC = json.dumps(
    {
        "layout": "quick",
        "headline": "Backlog is 5 lots",
        "kpis": [
            {"field": "active_lots", "label": "Lots", "format": "int"},
            {"field": "total_qty", "label": "Studs", "format": "qty"},
        ],
    }
)


def _block_dicts(blocks: list) -> list[dict[str, Any]]:
    return [b.to_dict() if hasattr(b, "to_dict") else b for b in blocks]


def test_business_prompt_asks_for_the_json_answer_spec(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes["/v1/metrics/backlog"] = BACKLOG
    llm = FakeChatLLM(replies=[BACKLOG_SPEC])
    make_orchestrator(llm).process_query("c", "whats the backlog today")
    assert "Reply with ONE JSON object" in llm.calls[0][0]["content"]


def test_json_spec_reply_renders_kpis_from_evidence(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/backlog"] = BACKLOG
    orch = make_orchestrator(FakeChatLLM(replies=[BACKLOG_SPEC]))

    result = orch.process_query("c", "whats the backlog today", is_dev=True)

    assert result.mode == "business-grounded"
    assert result.text == "Backlog is 5 lots"
    dicts = _block_dicts(result.blocks)
    assert dicts[0]["text"]["text"] == "*Backlog is 5 lots*"
    assert [f["text"] for f in dicts[1]["fields"]] == ["*Lots*\n5", "*Studs*\n1,000"]
    assert dicts[-1]["type"] == "context"
    assert result.dev_info is not None
    assert result.dev_info["answer_layout"] == "quick"


def test_prose_reply_still_renders_as_prose(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/backlog"] = BACKLOG
    orch = make_orchestrator(FakeChatLLM(replies=["Backlog is 5 lots."]))

    result = orch.process_query("c", "whats the backlog today", is_dev=True)

    assert result.text == "Backlog is 5 lots."
    assert _block_dicts(result.blocks)[0]["text"]["text"] == "Backlog is 5 lots."
    assert result.dev_info is not None
    assert result.dev_info["answer_layout"] == "prose"
    assert result.dev_info["spec_fallback_reason"] == "reply_not_a_spec"


def test_spec_answer_text_is_what_history_remembers(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/backlog"] = BACKLOG
    llm = FakeChatLLM(replies=[BACKLOG_SPEC, BACKLOG_SPEC])
    orch = make_orchestrator(llm)
    orch.process_query("c", "whats the backlog today")
    orch.process_query("c", "whats the backlog today")
    assert {"role": "assistant", "content": "Backlog is 5 lots"} in llm.calls[1]


def test_kill_switch_keeps_the_prose_prompt_and_output(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes["/v1/metrics/backlog"] = BACKLOG
    llm = FakeChatLLM(replies=[BACKLOG_SPEC])
    orch = make_orchestrator(llm, answer_spec_enabled=False)

    result = orch.process_query("c", "whats the backlog today")

    assert "Reply with ONE JSON object" not in llm.calls[0][0]["content"]
    assert result.text == BACKLOG_SPEC


RUNDOWN_SPEC = json.dumps(
    {
        "layout": "rundown",
        "headline": "Morning rundown",
        "sections": [
            {
                "title": "Heading",
                "headline": "70% of plan",
                "kpis": [{"source": "heading_overall", "field": "actual_studs", "label": "Studs", "format": "int"}],
            },
            {
                "title": "Backlog",
                "headline": "5 lots open",
                "kpis": [{"source": "backlog", "field": "active_lots", "label": "Lots", "format": "int"}],
            },
        ],
    }
)


def test_tool_router_renders_a_rundown_spec_from_every_tool(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes["/v1/metrics/heading/overall"] = HEADING_OVERALL
    fake_api.routes["/v1/metrics/backlog"] = BACKLOG
    llm = FakeLLM(
        tool_replies=[
            {
                "content": "",
                "tool_calls": [tool_call("heading_overall", {}, "c1"), tool_call("backlog", {}, "c2")],
            },
            {"content": RUNDOWN_SPEC},
        ]
    )
    orch = make_orchestrator(llm)

    result = orch.process_query("c", "give me the morning rundown", is_dev=True)

    assert "Reply with ONE JSON object" in llm.tool_calls[0]["messages"][0]["content"]
    assert result.mode == "business-tool-router"
    dicts = _block_dicts(result.blocks)
    assert [d["type"] for d in dicts].count("divider") == 2
    fields = [f["text"] for d in dicts for f in d.get("fields", [])]
    assert fields == ["*Studs*\n210,000", "*Lots*\n5"]
    assert "Heading: 70% of plan" in result.text
    assert result.dev_info is not None
    assert result.dev_info["answer_layout"] == "rundown"


def test_tool_router_prose_reply_still_works(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    llm = FakeLLM(tool_replies=[{"content": "Both look fine."}])
    result = make_orchestrator(llm).process_query("c", "how are backlog and heading looking", is_dev=True)
    assert result.text == "Both look fine."
    assert result.dev_info is not None
    assert result.dev_info["spec_fallback_reason"] == "reply_not_a_spec"


def test_tool_router_prompt_stays_prose_with_kill_switch(make_orchestrator: MakeOrch) -> None:
    llm = FakeLLM(tool_replies=[{"content": "ok"}])
    make_orchestrator(llm, answer_spec_enabled=False).process_query("c", "give me the morning rundown")
    assert "Reply with ONE JSON object" not in llm.tool_calls[0]["messages"][0]["content"]


# --- computed facts + comparison fetches --------------------------------------


def test_heading_fetches_prior_workday_and_offers_facts(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes["/v1/metrics/heading/overall"] = HEADING_OVERALL
    spec = json.dumps(
        {
            "layout": "quick",
            "headline": "70% of plan",
            "insights": [{"id": "heading_overall.vs_prior_day", "text": "Flat with yesterday."}],
        }
    )
    llm = FakeChatLLM(replies=[spec])

    result = make_orchestrator(llm).process_query("c", "hows heading going today", is_dev=True)

    assert fake_api.paths == ["/v1/metrics/heading/overall", "/v1/metrics/heading/overall"]
    assert fake_api.requests[1].url.params["data_date"] == "2026-10-06"
    prompt = llm.calls[0][-1]["content"]
    assert "Computed facts" in prompt and "heading_overall.vs_prior_day" in prompt
    assert "Flat with yesterday." in json.dumps(_block_dicts(result.blocks))
    assert result.dev_info is not None
    assert [f["id"] for f in result.dev_info["facts"]] == ["heading_overall.vs_prior_day"]


def test_failed_comparison_fetch_still_answers(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes.update(SALES_ROUTES)  # no /sage/trend route -> 503
    llm = FakeChatLLM(replies=[json.dumps({"layout": "quick", "headline": "Sales were $39,800"})])

    result = make_orchestrator(llm).process_query("c", "sales yesterday")

    assert "/v1/metrics/sage/trend" in fake_api.paths
    assert result.text == "Sales were $39,800"
    assert "sales.vs_avg" not in llm.calls[0][-1]["content"]


def test_kill_switch_makes_no_comparison_fetch(make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer) -> None:
    fake_api.routes["/v1/metrics/heading/overall"] = HEADING_OVERALL
    orch = make_orchestrator(FakeChatLLM(replies=["70% of plan."]), answer_spec_enabled=False)
    orch.process_query("c", "hows heading going today")
    assert fake_api.paths == ["/v1/metrics/heading/overall"]


def test_tool_results_carry_computed_facts_the_answer_can_cite(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes["/v1/metrics/backlog"] = {**BACKLOG, "overdue_lots": 2, "overdue_qty": 5000}
    spec = json.dumps(
        {
            "layout": "quick",
            "headline": "5 lots open",
            "insights": [{"id": "backlog.overdue", "text": "Two lots are overdue."}],
        }
    )
    llm = FakeLLM(
        tool_replies=[{"content": "", "tool_calls": [tool_call("backlog", {})]}, {"content": spec}],
    )

    result = make_orchestrator(llm).process_query("c", "give me the morning rundown")

    tool_message = llm.tool_calls[1]["messages"][-1]
    assert "backlog.overdue" in tool_message["content"]
    assert ":warning: Two lots are overdue." in json.dumps(_block_dicts(result.blocks))


def test_sales_evidence_warns_that_todays_trend_pct_is_a_partial_day(
    make_orchestrator: MakeOrch, fake_api: FakeFastAPIServer
) -> None:
    fake_api.routes.update(SALES_ROUTES)
    llm = FakeChatLLM(replies=["ok"])
    make_orchestrator(llm).process_query("c", "how are sales today")
    assert "partial day" in llm.calls[0][-1]["content"]
