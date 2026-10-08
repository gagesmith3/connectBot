"""
Computed facts: deterministic observations about FastAPI evidence (vs plan,
vs the prior day, share of total, items below minimum).

The LLM sees these as "Computed facts" and may phrase up to two of them as
insights, citing each by id. parse_answer_spec drops any insight whose id
isn't here, so the bot only ever states a trend this code worked out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .answer_renderer import format_value
from .api_client import FastAPIClient


@dataclass
class Fact:
    id: str
    text: str
    severity: str = "info"  # info | warn | good


def prior_workday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _day_label(day: date) -> str:
    return f"{day:%a} {day:%b} {day.day}"


def _pct_change(value: float, base: float) -> str:
    return f"{round((value - base) / base * 100):+d}%"


def _qty(value: float) -> str:
    return format_value(value, "qty")


def _int(value: float) -> str:
    return format_value(value, "int")


# --- comparison fetches -------------------------------------------------------


def comparison_intent(endpoint: str, params: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
    """The extra API call (if any) whose result compute_facts compares against."""
    if endpoint == "heading_overall":
        day = _day(data.get("data_date")) or date.today()
        return {"endpoint": "heading_overall", "parameters": {"data_date": prior_workday(day).isoformat()}}
    if endpoint == "sales":
        daily = data.get("daily") or {}
        day = _day(daily.get("data_date")) or date.today()
        return {"endpoint": "sage_trend", "parameters": {"days": 20, "end_date": prior_workday(day).isoformat()}}
    if endpoint == "backlog_breakdown":
        # A share of the whole backlog only means something for an unfiltered breakdown.
        if FastAPIClient._lot_filters(params):
            return None
        return {"endpoint": "backlog", "parameters": {}}
    return None


# --- facts per endpoint ---------------------------------------------------------


def _heading_overall(data: dict[str, Any], prior: dict[str, Any] | None) -> list[Fact]:
    facts: list[Fact] = []
    volume = _num(data.get("volume_pct"))
    shift = _num(data.get("shift_percent_complete"))
    in_shift = shift is not None and 0 < shift < 100
    if volume is not None and in_shift and shift is not None:
        gap = round(volume - shift)
        pace = "on pace" if gap == 0 else f"{'ahead of' if gap > 0 else 'behind'} pace by {abs(gap)} pts"
        severity = "warn" if gap <= -10 else "good" if gap >= 5 else "info"
        facts.append(
            Fact(
                "heading_overall.pace",
                f"{format_value(volume, 'pct')} of plan with {round(shift)}% of the shift done ({pace})",
                severity,
            )
        )
    projected_pct = _num(data.get("projected_volume_pct"))
    projected = _num(data.get("projected_studs"))
    if in_shift and projected_pct is not None and projected is not None:
        facts.append(
            Fact(
                "heading_overall.projection",
                f"Projected to finish at {format_value(projected_pct, 'pct')} of plan ({_int(projected)} studs)",
                "warn" if projected_pct < 90 else "info",
            )
        )
    actual_up, planned_up = _num(data.get("actual_uptime_pct")), _num(data.get("planned_uptime_pct"))
    if actual_up is not None and planned_up is not None:
        facts.append(
            Fact(
                "heading_overall.uptime",
                f"Uptime {format_value(actual_up, 'pct')} vs {format_value(planned_up, 'pct')} planned",
                "warn" if actual_up < planned_up - 10 else "info",
            )
        )
    prior_actual = _num((prior or {}).get("actual_studs"))
    prior_day = _day((prior or {}).get("data_date"))
    is_today = _day(data.get("data_date")) == date.today()
    if prior_actual and prior_day:
        value: float | None
        if in_shift and is_today and projected is not None:
            label, value = "Projected", projected
        else:
            label, value = "Actual", _num(data.get("actual_studs"))
        if value is not None:
            facts.append(
                Fact(
                    "heading_overall.vs_prior_day",
                    f"{label} {_int(value)} studs vs {_int(prior_actual)} on {_day_label(prior_day)} "
                    f"({_pct_change(value, prior_actual)})",
                )
            )
    return facts


def _heading(data: dict[str, Any], _: dict[str, Any] | None) -> list[Fact]:
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict) and _num(r.get("vs_plan_pct")) is not None]
    if not rows:
        return []
    latest = max(str(r.get("data_date", "")) for r in rows)
    heads = sorted((r for r in rows if str(r.get("data_date", "")) == latest), key=lambda r: r["vs_plan_pct"])
    if len(heads) < 2:
        return []
    worst, best = heads[0], heads[-1]
    facts = [
        Fact(
            "heading.best_worst",
            f"Best head: {best.get('head_name')} at {format_value(best['vs_plan_pct'], 'pct')} of plan; "
            f"worst: {worst.get('head_name')} at {format_value(worst['vs_plan_pct'], 'pct')}",
        )
    ]
    under = sum(1 for r in heads if r["vs_plan_pct"] < 80)
    if under:
        facts.append(Fact("heading.under_plan", f"{under} of {len(heads)} heads under 80% of plan", "warn"))
    return facts


def _sales(data: dict[str, Any], trend: dict[str, Any] | None) -> list[Fact]:
    daily = data.get("daily") or {}
    rows = [r for r in (trend or {}).get("rows") or [] if isinstance(r, dict)]
    day = _day(daily.get("data_date"))
    past_day = day if day is not None and day != date.today() else None
    facts: list[Fact] = []
    for fact_id, field, label in (
        ("sales.vs_avg", "sales_total", "Sales"),
        ("quotes.vs_avg", "quotes_total", "Quotes"),
    ):
        value = _num(daily.get(field))
        history = [v for v in (_num(r.get(field)) for r in rows) if v]
        if value is None or not history:
            continue
        average = sum(history) / len(history)
        window = f"a {format_value(average, 'usd')} daily average over the last {len(history)} business days"
        if past_day is None:
            text = f"{label} {format_value(value, 'usd')} so far today vs {window}"
        else:
            text = (
                f"{label} {format_value(value, 'usd')} on {_day_label(past_day)} vs {window} "
                f"({_pct_change(value, average)})"
            )
        facts.append(Fact(fact_id, text))
    return facts


def _backlog(data: dict[str, Any], _: dict[str, Any] | None) -> list[Fact]:
    facts: list[Fact] = []
    total = _num(data.get("total_qty"))
    stage, lots, qty = data.get("bottleneck_stage"), _num(data.get("bottleneck_lots")), _num(data.get("bottleneck_qty"))
    if stage and lots is not None and qty is not None:
        share = f" ({round(qty / total * 100)}% of backlog)" if total else ""
        facts.append(
            Fact(
                "backlog.bottleneck",
                f"{str(stage).capitalize()} is the bottleneck: {_int(lots)} lots, {_qty(qty)} studs{share}",
            )
        )
    overdue, overdue_qty = _num(data.get("overdue_lots")), _num(data.get("overdue_qty"))
    if overdue:
        studs = f" ({_qty(overdue_qty)} studs)" if overdue_qty else ""
        facts.append(Fact("backlog.overdue", f"{_int(overdue)} lots overdue{studs}", "warn"))
    return facts


def _backlog_breakdown(data: dict[str, Any], backlog: dict[str, Any] | None) -> list[Fact]:
    total = _num((backlog or {}).get("total_qty"))
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict) and _num(r.get("total_qty")) is not None]
    if not total or not rows:
        return []
    rows.sort(key=lambda r: r["total_qty"], reverse=True)
    top = rows[0]
    facts = [
        Fact(
            "backlog_breakdown.top_share",
            f"{top.get('group_value')} is {round(top['total_qty'] / total * 100)}% of the open backlog "
            f"({_qty(top['total_qty'])} of {_qty(total)} studs)",
        )
    ]
    if len(rows) >= 3:
        top3 = sum(r["total_qty"] for r in rows[:3])
        facts.append(
            Fact("backlog_breakdown.top3_share", f"The top 3 are {round(top3 / total * 100)}% of the open backlog")
        )
    return facts


def _equipment_parts(data: dict[str, Any], _: dict[str, Any] | None) -> list[Fact]:
    low, total = _num(data.get("low_count")), _num(data.get("total_parts"))
    if low is None or total is None:
        return []
    if low:
        return [Fact("equipment_parts.low", f"{_int(low)} of {_int(total)} build parts are below minimum", "warn")]
    return [Fact("equipment_parts.low", f"No build parts below minimum ({_int(total)} tracked)", "good")]


def _equipment_repairs(data: dict[str, Any], _: dict[str, Any] | None) -> list[Fact]:
    open_rows = [
        (received, r)
        for r in data.get("rows") or []
        if isinstance(r, dict)
        and str(r.get("status", "")).upper() != "CLOSED"
        and (received := _day(r.get("date_received"))) is not None
    ]
    if not open_rows:
        return []
    received, oldest = min(open_rows, key=lambda pair: pair[0])
    age = (date.today() - received).days
    when = f"{received:%b} {received.day}" + (f", {received.year}" if received.year != date.today().year else "")
    return [
        Fact(
            "equipment_repairs.oldest",
            f"Oldest open repair: {oldest.get('repair_id')} ({oldest.get('customer')}), received {when} — {age} days",
            "warn" if age > 30 else "info",
        )
    ]


def _livewire_demand(data: dict[str, Any], _: dict[str, Any] | None) -> list[Fact]:
    short = [
        r
        for r in data.get("rows") or []
        if isinstance(r, dict) and (r.get("shortage_flag") or (_num(r.get("delta_lbs")) or 0) < 0)
    ]
    if not short:
        return []
    short.sort(key=lambda r: _num(r.get("delta_lbs")) or 0)
    worst = short[0]
    deficit = sum(abs(_num(r.get("delta_lbs")) or 0) for r in short)
    name = worst.get("material_name") or worst.get("material_code")
    return [
        Fact(
            "livewire_demand.shortages",
            f"{len(short)} wire groups short, {_int(deficit)} lbs total; largest: {name} "
            f"{worst.get('wire_dia_min')}–{worst.get('wire_dia_max')} ({_int(_num(worst.get('delta_lbs')) or 0)} lbs)",
            "warn",
        )
    ]


_FACTS: dict[str, Callable[[dict[str, Any], dict[str, Any] | None], list[Fact]]] = {
    "heading_overall": _heading_overall,
    "heading": _heading,
    "sales": _sales,
    "backlog": _backlog,
    "backlog_breakdown": _backlog_breakdown,
    "equipment_parts": _equipment_parts,
    "equipment_repairs": _equipment_repairs,
    "livewire_demand": _livewire_demand,
}


def compute_facts(endpoint: str, data: dict[str, Any], comparison: dict[str, Any] | None = None) -> list[Fact]:
    function = _FACTS.get(endpoint)
    if function is None or not isinstance(data, dict):
        return []
    return function(data, comparison)
