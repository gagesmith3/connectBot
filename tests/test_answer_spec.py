"""parse_answer_spec: the LLM's JSON answer is validated against the evidence it was given."""

from __future__ import annotations

import json
from typing import Any

from src.connectbot.answer_spec import parse_answer_spec

BREAKDOWN = {
    "group_by": "stud_size",
    "snapshot_ts": "2026-10-07T14:30:00",
    "rows": [
        {"group_value": "1/4-20", "lot_count": 27, "total_qty": 3855000},
        {"group_value": "10-24", "lot_count": 14, "total_qty": 2363500},
    ],
}
SALES = {"daily": {"data_date": "2026-10-07", "sales_total": 6500.92}, "summary": None}


def _spec(**fields: Any) -> str:
    return json.dumps({"layout": "table", "headline": "Backlog by size", **fields})


def test_parses_a_table_spec_and_defaults_source_to_the_only_evidence() -> None:
    raw = _spec(table={"columns": [{"field": "group_value", "label": "Size"}], "sort": "-total_qty", "limit": 5})

    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN})

    assert spec is not None
    assert spec.layout == "table"
    assert spec.table is not None
    assert spec.table.source == "backlog_breakdown"
    assert [c.field for c in spec.table.columns] == ["group_value"]
    assert spec.table.sort == "-total_qty"


def test_accepts_json_wrapped_in_a_code_fence_and_chatter() -> None:
    raw = "Here you go:\n```json\n" + _spec(layout="quick") + "\n```"
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN})
    assert spec is not None
    assert spec.layout == "quick"


def test_returns_none_for_plain_prose() -> None:
    assert parse_answer_spec("Backlog is 312 lots.", {"backlog": {}}) is None


def test_returns_none_when_json_has_no_headline_or_body() -> None:
    assert parse_answer_spec('{"layout": "quick"}', {"backlog": {}}) is None


def test_drops_columns_that_are_not_in_the_rows() -> None:
    raw = _spec(
        table={
            "columns": [
                {"field": "group_value", "label": "Size"},
                {"field": "made_up_field", "label": "Fake"},
            ]
        }
    )
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN})
    assert spec is not None and spec.table is not None
    assert [c.field for c in spec.table.columns] == ["group_value"]


def test_ignores_a_sort_on_an_unknown_field() -> None:
    raw = _spec(table={"columns": [{"field": "group_value"}], "sort": "-nope"})
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN})
    assert spec is not None and spec.table is not None
    assert spec.table.sort is None


def test_clamps_table_limit_to_15() -> None:
    raw = _spec(table={"columns": [{"field": "group_value"}], "limit": 500})
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN})
    assert spec is not None and spec.table is not None
    assert spec.table.limit == 15


def test_table_layout_without_a_usable_table_downgrades() -> None:
    raw = _spec(table={"columns": [{"field": "nope"}]})
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN})
    assert spec is not None
    assert spec.table is None
    assert spec.layout == "quick"


def test_table_from_an_unknown_source_is_dropped() -> None:
    raw = _spec(table={"source": "sales", "columns": [{"field": "group_value"}]})
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN, "backlog": {}})
    assert spec is not None
    assert spec.table is None


def test_kpis_resolve_dotted_fields_and_drop_missing_ones() -> None:
    raw = json.dumps(
        {
            "layout": "quick",
            "headline": "Sales so far",
            "kpis": [
                {"field": "daily.sales_total", "label": "Sales", "format": "usd"},
                {"field": "daily.invented", "label": "Nope"},
                {"field": "summary.sales_trend_pct", "label": "Trend"},
            ],
        }
    )
    spec = parse_answer_spec(raw, {"sales": SALES})
    assert spec is not None
    assert [(k.source, k.field, k.format) for k in spec.kpis] == [("sales", "daily.sales_total", "usd")]


def test_unknown_format_becomes_text() -> None:
    raw = json.dumps({"layout": "quick", "headline": "x", "kpis": [{"field": "daily.sales_total", "format": "euros"}]})
    spec = parse_answer_spec(raw, {"sales": SALES})
    assert spec is not None
    assert spec.kpis[0].format == "text"


def test_unknown_layout_falls_back_to_prose() -> None:
    raw = json.dumps({"layout": "infographic", "headline": "x", "body": "Backlog is fine."})
    spec = parse_answer_spec(raw, {"backlog": {}})
    assert spec is not None
    assert spec.layout == "prose"


def test_insights_must_cite_a_computed_fact() -> None:
    raw = json.dumps(
        {
            "layout": "quick",
            "headline": "x",
            "insights": [
                {"id": "heading_overall.vs_plan", "text": "Running 26% behind plan."},
                {"id": "made.up", "text": "Best week ever."},
            ],
        }
    )
    spec = parse_answer_spec(raw, {"heading_overall": {}}, fact_ids={"heading_overall.vs_plan"})
    assert spec is not None
    assert [i.id for i in spec.insights] == ["heading_overall.vs_plan"]


def test_at_most_two_insights() -> None:
    ids = {"a", "b", "c"}
    raw = json.dumps({"layout": "quick", "headline": "x", "insights": [{"id": i, "text": i} for i in sorted(ids)]})
    spec = parse_answer_spec(raw, {"backlog": {}}, fact_ids=ids)
    assert spec is not None
    assert len(spec.insights) == 2


def test_rundown_sections_validate_against_their_own_source() -> None:
    raw = json.dumps(
        {
            "layout": "rundown",
            "headline": "Morning rundown",
            "sections": [
                {
                    "title": "Backlog",
                    "headline": "Top sizes",
                    "table": {"source": "backlog_breakdown", "columns": [{"field": "group_value"}]},
                },
                {
                    "title": "Sales",
                    "headline": "Sales so far",
                    "kpis": [{"source": "sales", "field": "daily.sales_total", "format": "usd"}],
                },
                {"title": "Ghost", "headline": "", "kpis": [{"source": "nope", "field": "x"}]},
            ],
        }
    )
    spec = parse_answer_spec(raw, {"backlog_breakdown": BREAKDOWN, "sales": SALES})
    assert spec is not None
    assert spec.layout == "rundown"
    assert [s.title for s in spec.sections] == ["Backlog", "Sales"]
    assert spec.sections[0].table is not None
    assert spec.sections[1].kpis[0].field == "daily.sales_total"


def test_rundown_without_usable_sections_downgrades() -> None:
    raw = json.dumps({"layout": "rundown", "headline": "Rundown", "sections": []})
    spec = parse_answer_spec(raw, {"backlog": {}})
    assert spec is not None
    assert spec.layout == "quick"
