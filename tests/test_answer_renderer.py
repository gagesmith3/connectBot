"""answer_renderer: an AnswerSpec plus evidence becomes Slack blocks.

Values in KPIs and tables come from the evidence; the spec only names fields.
"""

from __future__ import annotations

import json
from typing import Any

from src.connectbot.answer_renderer import format_value, freshness_label, render
from src.connectbot.answer_spec import AnswerSpec, parse_answer_spec

BREAKDOWN = {
    "group_by": "stud_size",
    "snapshot_ts": "2026-10-07T14:30:00",
    "rows": [
        {"group_value": "10-24", "lot_count": 14, "total_qty": 2363500},
        {"group_value": "1/4-20 X 1 CD FLANGED ALUMINUM 5000 SERIES", "lot_count": 27, "total_qty": 3855000},
        {"group_value": "8-32", "lot_count": 11, "total_qty": 2059000},
    ],
}
HEADING = {"data_date": "2026-10-07", "plan_studs": 320231, "actual_studs": 235495, "volume_pct": 73.54}


def _dicts(blocks: list) -> list[dict[str, Any]]:
    return [b.to_dict() if hasattr(b, "to_dict") else b for b in blocks]


def _all_text(blocks: list) -> str:
    return json.dumps(_dicts(blocks), ensure_ascii=False)


def _spec(raw: dict[str, Any], evidence: dict[str, Any], fact_ids: set[str] | None = None) -> AnswerSpec:
    spec = parse_answer_spec(json.dumps(raw), evidence, fact_ids)
    assert spec is not None
    return spec


def _code_block(blocks: list) -> str:
    for block in _dicts(blocks):
        text = (block.get("text") or {}).get("text", "")
        if text.startswith("```"):
            return text.strip("`")
    raise AssertionError("no code block")


# --- value formatting ---------------------------------------------------------


def test_format_value() -> None:
    assert format_value(3855000, "int") == "3,855,000"
    assert format_value(2363500, "qty") == "2.36M"
    assert format_value(3855000, "qty") == "3.86M"  # half-up, not binary-float 3.85
    assert format_value(235495, "qty") == "235K"
    assert format_value(950, "qty") == "950"
    assert format_value(73.54, "pct") == "74%"
    assert format_value(6500.92, "usd") == "$6,501"
    assert format_value("2026-10-07", "date") == "Oct 7"
    assert format_value(None, "int") == "—"
    assert format_value("n/a", "int") == "n/a"


def test_freshness_label() -> None:
    assert freshness_label({"snapshot_ts": "2026-10-07T14:30:00"}) == "as of Oct 7, 2:30 PM"
    assert freshness_label({"data_date": "2026-10-06"}) == "for Oct 6"
    assert freshness_label({"daily": {"updated_at": "2026-10-07T09:05:00"}}) == "as of Oct 7, 9:05 AM"
    assert freshness_label({}) == ""


# --- quick --------------------------------------------------------------------


def test_quick_renders_headline_kpi_fields_from_evidence_and_footer() -> None:
    evidence = {"heading_overall": HEADING}
    spec = _spec(
        {
            "layout": "quick",
            "headline": "We're at 74% of today's plan",
            "kpis": [
                {"field": "actual_studs", "label": "Studs", "format": "qty"},
                {"field": "volume_pct", "label": "Vs plan", "format": "pct"},
            ],
        },
        evidence,
    )

    text, blocks = render(spec, evidence)
    dicts = _dicts(blocks)

    assert dicts[0]["text"]["text"] == "*We're at 74% of today's plan*"
    fields = [f["text"] for f in dicts[1]["fields"]]
    assert fields == ["*Studs*\n235K", "*Vs plan*\n74%"]
    assert dicts[-1]["type"] == "context"
    assert "heading overall · for Oct 7" in dicts[-1]["elements"][0]["text"]
    assert text.startswith("We're at 74% of today's plan")


def test_insights_render_under_the_answer_and_join_the_fallback_text() -> None:
    evidence = {"heading_overall": HEADING}
    spec = _spec(
        {
            "layout": "quick",
            "headline": "74% of plan",
            "insights": [{"id": "heading_overall.vs_plan", "text": "Running 26% behind plan."}],
        },
        evidence,
        fact_ids={"heading_overall.vs_plan"},
    )
    text, blocks = render(spec, evidence)
    assert "Running 26% behind plan." in _all_text(blocks)
    assert "Running 26% behind plan." in text


# --- table --------------------------------------------------------------------


def test_table_values_come_from_evidence_sorted_and_limited() -> None:
    evidence = {"backlog_breakdown": BREAKDOWN}
    spec = _spec(
        {
            "layout": "table",
            "headline": "Backlog by stud size",
            "table": {
                "columns": [
                    {"field": "group_value", "label": "Size"},
                    {"field": "total_qty", "label": "Qty", "format": "int"},
                ],
                "sort": "-total_qty",
                "limit": 2,
            },
        },
        evidence,
    )

    _, blocks = render(spec, evidence)
    lines = _code_block(blocks).splitlines()

    assert lines[0].split() == ["Size", "Qty"]
    assert lines[2].endswith("3,855,000")
    assert lines[3].startswith("10-24") and lines[3].endswith("2,363,500")
    assert len(lines) == 4  # header, rule, 2 rows
    assert "…and 1 more" in _all_text(blocks)


def test_table_fits_a_phone_and_truncates_long_labels() -> None:
    evidence = {"backlog_breakdown": BREAKDOWN}
    spec = _spec(
        {
            "layout": "table",
            "headline": "x",
            "table": {
                "columns": [
                    {"field": "group_value", "label": "Size"},
                    {"field": "lot_count", "label": "Lots", "format": "int"},
                    {"field": "total_qty", "label": "Qty", "format": "int"},
                ]
            },
        },
        evidence,
    )
    lines = _code_block(render(spec, evidence)[1]).splitlines()
    assert all(len(line) <= 44 for line in lines)
    assert any("…" in line for line in lines)


def test_numbers_in_the_spec_never_reach_the_table() -> None:
    evidence = {"backlog_breakdown": BREAKDOWN}
    raw = {
        "layout": "table",
        "headline": "x",
        "table": {"columns": [{"field": "group_value"}, {"field": "total_qty", "value": 999999999}]},
    }
    _, blocks = render(_spec(raw, evidence), evidence)
    assert "999" not in _code_block(blocks)


# --- rundown ------------------------------------------------------------------


def test_rundown_renders_each_section_with_a_divider_between() -> None:
    evidence = {"heading_overall": HEADING, "backlog_breakdown": BREAKDOWN}
    spec = _spec(
        {
            "layout": "rundown",
            "headline": "Morning rundown",
            "sections": [
                {
                    "title": "Heading",
                    "headline": "74% of plan",
                    "kpis": [{"source": "heading_overall", "field": "actual_studs", "format": "qty"}],
                },
                {
                    "title": "Backlog",
                    "headline": "1/4-20 leads",
                    "table": {"source": "backlog_breakdown", "columns": [{"field": "group_value"}]},
                },
            ],
        },
        evidence,
    )
    text, blocks = render(spec, evidence)
    dicts = _dicts(blocks)
    types = [d["type"] for d in dicts]

    assert dicts[0]["text"]["text"] == "*Morning rundown*"
    assert types.count("divider") == 2
    assert "*Heading*" in _all_text(blocks) and "*Backlog*" in _all_text(blocks)
    assert "heading overall" in dicts[-1]["elements"][0]["text"]
    assert "backlog breakdown" in dicts[-1]["elements"][0]["text"]
    assert "Heading: 74% of plan" in text


# --- prose / limits -----------------------------------------------------------


def test_prose_renders_headline_and_body() -> None:
    evidence = {"backlog": {"snapshot_ts": "2026-10-07T06:00:00"}}
    spec = _spec({"layout": "prose", "headline": "Backlog is steady", "body": "Nothing unusual today."}, evidence)
    text, blocks = render(spec, evidence)
    assert "*Backlog is steady*" in _all_text(blocks)
    assert "Nothing unusual today." in _all_text(blocks)
    assert text == "Backlog is steady\nNothing unusual today."


def test_markdown_double_bold_is_fixed() -> None:
    evidence = {"backlog": {}}
    spec = _spec({"layout": "prose", "headline": "x", "body": "We're **70%** there"}, evidence)
    assert "*70%*" in _all_text(render(spec, evidence)[1])


def test_long_body_stays_within_slack_section_limit() -> None:
    evidence = {"backlog": {}}
    spec = _spec({"layout": "prose", "headline": "x", "body": "word " * 600}, evidence)
    for block in _dicts(render(spec, evidence)[1]):
        assert len((block.get("text") or {}).get("text", "")) <= 3000


def test_no_evidence_means_no_footer() -> None:
    spec = _spec({"layout": "prose", "headline": "Hi"}, {})
    _, blocks = render(spec, {})
    assert [d["type"] for d in _dicts(blocks)] == ["section"]


def test_warning_insights_get_a_warning_marker() -> None:
    evidence = {"backlog": {}}
    spec = _spec(
        {"layout": "quick", "headline": "x", "insights": [{"id": "a", "text": "Late."}, {"id": "b", "text": "Fine."}]},
        evidence,
        fact_ids={"a", "b"},
    )
    text = _all_text(render(spec, evidence, severities={"a": "warn"})[1])
    assert ":warning: Late." in text
    assert ":bulb: Fine." in text


def test_dates_outside_this_year_show_the_year() -> None:
    assert format_value("2025-08-19", "date") == "Aug 19, 2025"
    assert freshness_label({"data_date": "2025-12-31"}) == "for Dec 31, 2025"


def test_footer_lists_each_source_once() -> None:
    day = {"data_date": "2026-10-07"}
    evidence = {"heading": day, "heading#2": day, "heading#3": {"data_date": "2026-10-01"}}
    spec = _spec({"layout": "prose", "headline": "x"}, evidence)
    footer = _dicts(render(spec, evidence)[1])[-1]["elements"][0]["text"]
    assert footer == "heading · for Oct 7  |  heading · for Oct 1"
