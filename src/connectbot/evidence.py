"""Evidence handling: shrink FastAPI payloads for the LLM and summarize them for
[DEV] traces.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from urllib.parse import urlencode


def trim_evidence(endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
    """Reduce large row sets before sending evidence to the LLM.

    The heading endpoint returns ~500 rows spanning weeks of history; dumping
    it all into the prompt is slow, expensive, and drowns the answer. Rows
    arrive newest-first, so keep only the most recent slice.
    """
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) <= 80:
        return data

    if endpoint == "heading":
        # Keep the 7 most recent data_dates (covers "today" and week trends).
        recent_dates: list[str] = []
        trimmed_rows: list[dict[str, Any]] = []
        for row in rows:
            row_date = str(row.get("data_date", ""))
            if row_date not in recent_dates:
                if len(recent_dates) >= 7:
                    break
                recent_dates.append(row_date)
            trimmed_rows.append(row)
        trimmed_rows = trimmed_rows[:80]
    else:
        trimmed_rows = rows[:80]

    return {
        **data,
        "rows": trimmed_rows,
        "evidence_note": (
            f"Showing the {len(trimmed_rows)} most recent of {len(rows)} total rows "
            "(newest first); older history omitted."
        ),
    }


def build_request_preview(base_url: str, endpoint: str | None, params: dict[str, Any]) -> dict[str, Any] | None:
    path_by_endpoint = {
        "backlog": "/v1/metrics/backlog",
        "backlog_breakdown": "/v1/metrics/backlog/breakdown",
        "backlog_lots": "/v1/metrics/backlog/lots",
        "heading": "/v1/metrics/heading",
        "heading_overall": "/v1/metrics/heading/overall",
        "heading_summary": "/v1/metrics/heading/summary",
        "livewire_demand": "/v1/metrics/livewire/demand",
        "livewire_inventory": "/v1/metrics/livewire/inventory",
        "livewire_usage": "/v1/metrics/livewire/usage",
        "sales": "/v1/metrics/sage/daily + /v1/metrics/sage/summary",
        "sage_trend": "/v1/metrics/sage/trend",
        "equipment_sold": "/v1/metrics/equipment/sold",
        "equipment_stock": "/v1/metrics/equipment/stock",
        "equipment_builds": "/v1/metrics/equipment/builds",
        "equipment_parts": "/v1/metrics/equipment/parts",
        "equipment_repairs": "/v1/metrics/equipment/repairs",
        "equipment_repairs_history": "/v1/metrics/equipment/repairs/history",
        "compute_runs": "/v1/metrics/compute/runs",
    }
    if not endpoint:
        return None
    path = path_by_endpoint.get(endpoint, "unknown")
    encoded = urlencode(params, doseq=True)
    url = f"{base_url}{path}"
    if encoded:
        url = f"{url}?{encoded}"
    return {
        "method": "GET",
        "path": path,
        "url": url,
        "params": params,
    }


def build_response_preview(data: dict[str, Any]) -> dict[str, Any]:
    preview: dict[str, Any] = {"keys": sorted(list(data.keys()))}
    if "count" in data:
        preview["count"] = data.get("count")
    rows = data.get("rows")
    if isinstance(rows, list):
        preview["rows_count"] = len(rows)
        preview["rows_sample"] = rows[:2]
        if rows and all(isinstance(row, dict) for row in rows):
            shortage_rows = [row for row in rows if bool(row.get("shortage_flag"))]
            preview["shortage_rows"] = len(shortage_rows)
            preview["non_shortage_rows"] = len(rows) - len(shortage_rows)
            delta_values = [float(row["delta_lbs"]) for row in rows if row.get("delta_lbs") is not None]
            if delta_values:
                preview["delta_lbs_min"] = min(delta_values)
                preview["delta_lbs_max"] = max(delta_values)
    return preview


def build_evidence_lines(endpoint: str, data: dict[str, Any]) -> list[str]:
    if endpoint == "backlog":
        return [
            f"Active lots: {data.get('active_lots', 'n/a')}",
            f"Total qty: {data.get('total_qty', 'n/a')}",
            f"Heading queue qty: {data.get('heading_qty', 'n/a')}",
            f"Snapshot: {data.get('snapshot_ts', 'n/a')}",
        ]
    if endpoint == "heading":
        return [f"Rows returned: {data.get('count', 0)}"]
    if endpoint == "heading_overall":
        return [
            f"Plan studs: {data.get('plan_studs', 'n/a')}",
            f"Actual studs: {data.get('actual_studs', 'n/a')}",
            f"Volume %: {data.get('volume_pct', 'n/a')}",
            f"Running headers: {data.get('running_headers_count', 'n/a')}",
        ]
    if endpoint == "livewire_demand":
        rows = data.get("rows", [])
        if not rows:
            return ["No livewire demand rows returned"]
        first = rows[0]
        return [
            f"Rows returned: {data.get('count', 0)}",
            f"Material: {first.get('material_code', 'n/a')} ({first.get('material_name', 'n/a')})",
            f"Top shortage delta lbs: {first.get('delta_lbs', 'n/a')}",
            f"Snapshot: {first.get('snapshot_ts', 'n/a')}",
        ]
    if endpoint == "livewire_inventory":
        rows = data.get("rows", [])
        total_weight = round(sum(float(r.get("weight_lbs") or 0) for r in rows), 2)
        total_spools = sum(int(r.get("spool_count") or 0) for r in rows)
        return [
            f"Groups returned: {data.get('count', 0)}",
            f"Total weight lbs: {total_weight}",
            f"Total spools: {total_spools}",
            f"Location filter: {data.get('location') or 'all'}",
        ]
    if endpoint == "livewire_usage":
        return [
            f"Rows returned: {data.get('count', 0)}",
            f"Dimension filter: {data.get('dimension') or 'all'}",
        ]
    if endpoint == "sales":
        daily = data.get("daily") or {}
        summary = data.get("summary") or {}
        return [
            f"Data date: {daily.get('data_date') or summary.get('data_date') or 'n/a'}",
            f"Sales today: ${daily.get('sales_total', 'n/a')} across {daily.get('sales_orders', 'n/a')} orders",
            f"Quotes today: ${daily.get('quotes_total', 'n/a')}",
            f"Sales vs prev business day: {summary.get('sales_trend_pct', 'n/a')}%",
        ]
    if endpoint == "equipment_sold":

        def _units(value: Any) -> str:
            try:
                return f"{float(value):g}"
            except (TypeError, ValueError):
                return "n/a"

        rows = data.get("rows", [])
        top = ", ".join(f"{r.get('label')} x{_units(r.get('units'))}" for r in rows[:3] if r.get("units"))
        return [
            f"Window: {data.get('window')} ({data.get('start_date')} to {data.get('end_date')})",
            f"Machines sold: {_units(data.get('machine_units'))}",
            f"All equipment units: {_units(data.get('total_units'))}",
            f"Top: {top or 'none'}",
        ]
    if endpoint == "equipment_stock":
        by_type = ", ".join(f"{t.get('equip_type')}: {t.get('units')}" for t in data.get("by_type", []))
        return [
            f"Ready units in stock: {data.get('total_units', 'n/a')}",
            f"By type: {by_type or 'none'}",
            f"Snapshot: {data.get('snapshot_ts', 'n/a')}",
        ]
    if endpoint == "equipment_builds":
        return [
            f"Open build requests: {data.get('count', 0)}",
            f"Owed (unbuilt) units: {data.get('owed_units_total', 0)}",
            f"Snapshot: {data.get('snapshot_ts', 'n/a')}",
        ]
    if endpoint == "equipment_parts":
        return [
            f"Parts below minimum: {data.get('low_count', 0)} of {data.get('total_parts', 0)}",
            f"Snapshot: {data.get('snapshot_ts', 'n/a')}",
        ]
    if endpoint == "equipment_repairs":
        rows = data.get("rows", [])
        by_stage: dict[str, int] = defaultdict(int)
        for r in rows:
            by_stage[r.get("stage") or "n/a"] += 1
        stage_breakdown = ", ".join(f"{k}: {v}" for k, v in by_stage.items())
        return [
            f"Open repairs: {data.get('count', 0)}",
            f"By stage: {stage_breakdown or 'none'}",
            f"Snapshot: {data.get('snapshot_ts', 'n/a')}",
        ]
    if endpoint == "equipment_repairs_history":
        return [
            f"Closed repairs returned: {data.get('count', 0)}",
            f"Customer filter: {data.get('customer') or 'all'}",
            f"Date range: {data.get('start_date') or 'any'} to {data.get('end_date') or 'any'}",
            f"Snapshot: {data.get('snapshot_ts', 'n/a')}",
        ]
    return []
