"""insights: deterministic facts computed from FastAPI evidence for the LLM to phrase."""

from __future__ import annotations

from datetime import date

from src.connectbot.insights import comparison_intent, compute_facts, prior_workday

HEADING_OVERALL = {
    "data_date": "2026-10-07",
    "plan_studs": 320231,
    "actual_studs": 235495,
    "volume_pct": 73.54,
    "planned_uptime_pct": 84.31,
    "actual_uptime_pct": 61.9,
    "shift_percent_complete": 71,
    "projected_studs": 331775,
    "projected_volume_pct": 104,
}


def _facts(endpoint: str, data: dict, comparison: dict | None = None) -> dict[str, str]:
    return {f.id: f.text for f in compute_facts(endpoint, data, comparison)}


def _severity(endpoint: str, data: dict, comparison: dict | None = None) -> dict[str, str]:
    return {f.id: f.severity for f in compute_facts(endpoint, data, comparison)}


# --- dates --------------------------------------------------------------------


def test_prior_workday_skips_weekends() -> None:
    assert prior_workday(date(2026, 10, 7)) == date(2026, 10, 6)  # Wed -> Tue
    assert prior_workday(date(2026, 10, 5)) == date(2026, 10, 2)  # Mon -> Fri
    assert prior_workday(date(2026, 10, 4)) == date(2026, 10, 2)  # Sun -> Fri


# --- comparison fetches ---------------------------------------------------------


def test_heading_overall_compares_with_the_prior_workday() -> None:
    assert comparison_intent("heading_overall", {}, HEADING_OVERALL) == {
        "endpoint": "heading_overall",
        "parameters": {"data_date": "2026-10-06"},
    }


def test_sales_compares_with_a_trailing_20_day_series_ending_the_day_before() -> None:
    data = {"daily": {"data_date": "2026-10-05"}, "summary": None}
    assert comparison_intent("sales", {}, data) == {
        "endpoint": "sage_trend",
        "parameters": {"days": 20, "end_date": "2026-10-02"},
    }


def test_unfiltered_breakdown_fetches_the_backlog_total() -> None:
    assert comparison_intent("backlog_breakdown", {"group_by": "stud_size"}, {}) == {
        "endpoint": "backlog",
        "parameters": {},
    }


def test_filtered_breakdown_has_no_comparison() -> None:
    params = {"group_by": "stud_length", "stud_size": "1/4-20"}
    assert comparison_intent("backlog_breakdown", params, {}) is None


def test_endpoints_without_a_comparison() -> None:
    assert comparison_intent("equipment_parts", {}, {}) is None


# --- heading ------------------------------------------------------------------


def test_heading_overall_facts() -> None:
    facts = _facts("heading_overall", HEADING_OVERALL)
    assert facts["heading_overall.pace"] == "74% of plan with 71% of the shift done (ahead of pace by 3 pts)"
    assert facts["heading_overall.projection"] == "Projected to finish at 104% of plan (331,775 studs)"
    assert facts["heading_overall.uptime"] == "Uptime 62% vs 84% planned"
    assert _severity("heading_overall", HEADING_OVERALL)["heading_overall.uptime"] == "warn"


def test_heading_overall_compares_projection_with_yesterdays_actual() -> None:
    prior = {"data_date": "2026-10-06", "actual_studs": 300000}
    facts = _facts("heading_overall", HEADING_OVERALL, prior)
    assert facts["heading_overall.vs_prior_day"] == "Projected 331,775 studs vs 300,000 on Tue Oct 6 (+11%)"


def test_finished_day_compares_actual_with_actual() -> None:
    finished = {"data_date": "2026-10-06", "actual_studs": 270000, "shift_percent_complete": 100}
    prior = {"data_date": "2026-10-05", "actual_studs": 300000}
    facts = _facts("heading_overall", finished, prior)
    assert facts["heading_overall.vs_prior_day"] == "Actual 270,000 studs vs 300,000 on Mon Oct 5 (-10%)"
    assert "heading_overall.pace" not in facts


def test_heading_rows_best_and_worst_head() -> None:
    data = {
        "rows": [
            {"data_date": "2026-10-07", "head_name": "CARLO_SALVI", "vs_plan_pct": 161.6},
            {"data_date": "2026-10-07", "head_name": "NATIONAL_2", "vs_plan_pct": 40.2},
            {"data_date": "2026-10-07", "head_name": "FENG_PEI_2", "vs_plan_pct": 111.5},
            {"data_date": "2026-10-06", "head_name": "OLD", "vs_plan_pct": 5},
        ]
    }
    facts = _facts("heading", data)
    assert facts["heading.best_worst"] == "Best head: CARLO_SALVI at 162% of plan; worst: NATIONAL_2 at 40%"
    assert facts["heading.under_plan"] == "1 of 3 heads under 80% of plan"


# --- sales --------------------------------------------------------------------


def test_sales_vs_trailing_average_today_is_so_far() -> None:
    data = {"daily": {"data_date": "2026-10-07", "sales_total": 6500.92, "quotes_total": 18988.78}, "summary": {}}
    trend = {
        "rows": [
            {"data_date": "2026-10-05", "sales_total": 20000, "quotes_total": 40000},
            {"data_date": "2026-10-06", "sales_total": 30000, "quotes_total": 20000},
            {"data_date": "2026-09-07", "sales_total": 0, "quotes_total": 0},  # holiday: ignored
        ]
    }
    facts = _facts("sales", data, trend)
    assert facts["sales.vs_avg"] == "Sales $6,501 so far today vs a $25,000 daily average over the last 2 business days"
    assert facts["quotes.vs_avg"] == (
        "Quotes $18,989 so far today vs a $30,000 daily average over the last 2 business days"
    )


def test_past_sales_day_is_not_so_far() -> None:
    data = {"daily": {"data_date": "2026-10-06", "sales_total": 30000}, "summary": {}}
    trend = {"rows": [{"sales_total": 20000}]}
    assert _facts("sales", data, trend)["sales.vs_avg"] == (
        "Sales $30,000 on Tue Oct 6 vs a $20,000 daily average over the last 1 business days (+50%)"
    )


def test_sales_without_comparison_has_no_average_fact() -> None:
    assert "sales.vs_avg" not in _facts("sales", {"daily": {"data_date": "2026-10-07", "sales_total": 5}})


# --- backlog ------------------------------------------------------------------


def test_backlog_bottleneck_and_overdue() -> None:
    data = {
        "total_qty": 15184300,
        "bottleneck_stage": "plate",
        "bottleneck_lots": 103,
        "bottleneck_qty": 7813000,
        "overdue_lots": 4,
        "overdue_qty": 120000,
    }
    facts = _facts("backlog", data)
    assert facts["backlog.bottleneck"] == "Plate is the bottleneck: 103 lots, 7.81M studs (51% of backlog)"
    assert facts["backlog.overdue"] == "4 lots overdue (120K studs)"
    assert _severity("backlog", data)["backlog.overdue"] == "warn"


def test_breakdown_share_of_total_backlog() -> None:
    data = {
        "rows": [
            {"group_value": "1/4-20", "total_qty": 3855000},
            {"group_value": "10-24", "total_qty": 2363500},
            {"group_value": "8-32", "total_qty": 2059000},
            {"group_value": "M6", "total_qty": 1330000},
        ]
    }
    facts = _facts("backlog_breakdown", data, {"total_qty": 15184300})
    assert facts["backlog_breakdown.top_share"] == "1/4-20 is 25% of the open backlog (3.86M of 15.18M studs)"
    assert facts["backlog_breakdown.top3_share"] == "The top 3 are 55% of the open backlog"


def test_breakdown_without_total_has_no_share() -> None:
    assert _facts("backlog_breakdown", {"rows": [{"group_value": "x", "total_qty": 1}]}) == {}


# --- equipment / wire ---------------------------------------------------------


def test_parts_below_minimum() -> None:
    assert _facts("equipment_parts", {"low_count": 3, "total_parts": 268}) == {
        "equipment_parts.low": "3 of 268 build parts are below minimum"
    }
    assert _facts("equipment_parts", {"low_count": 0, "total_parts": 268}) == {
        "equipment_parts.low": "No build parts below minimum (268 tracked)"
    }


def test_oldest_open_repair() -> None:
    data = {
        "rows": [
            {"repair_id": "EREP-127", "customer": "HYBROCO", "status": "OPEN", "date_received": "2026-08-19"},
            {"repair_id": "EREP-200", "customer": "ACME", "status": "OPEN", "date_received": "2026-10-01"},
            {"repair_id": "EREP-1", "customer": "OLD", "status": "CLOSED", "date_received": "2025-01-01"},
        ]
    }
    assert _facts("equipment_repairs", data)["equipment_repairs.oldest"] == (
        "Oldest open repair: EREP-127 (HYBROCO), received Aug 19 — 49 days"
    )


def test_wire_shortages() -> None:
    data = {
        "rows": [
            {"material_name": "Stainless Steel", "wire_dia_min": 0.328, "wire_dia_max": 0.34, "delta_lbs": -4782.4},
            {"material_name": "Mild Steel", "wire_dia_min": 0.328, "wire_dia_max": 0.34, "delta_lbs": -2130.34},
            {"material_name": "Copper", "wire_dia_min": 0.1, "wire_dia_max": 0.2, "delta_lbs": 500},
        ]
    }
    assert _facts("livewire_demand", data)["livewire_demand.shortages"] == (
        "2 wire groups short, 6,913 lbs total; largest: Stainless Steel 0.328–0.34 (-4,782 lbs)"
    )


def test_unknown_endpoint_and_bad_data_give_no_facts() -> None:
    assert compute_facts("compute_runs", {"rows": []}) == []
    assert compute_facts("heading_overall", {"volume_pct": "n/a"}) == []
