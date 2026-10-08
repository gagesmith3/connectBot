"""
LLM tool-calling router: lets the model pick one or more FastAPI endpoints
and synthesize a single grounded answer (e.g. a morning rundown that combines
heading, sales, and backlog).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_DATE_PARAM = {
    "type": "string",
    "description": "Optional ISO date YYYY-MM-DD. Omit for the latest data (today).",
}

# Tool names must match the orchestrator's endpoint names — results are
# fetched through the same _call_api path the keyword router uses.
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "backlog",
            "description": "Current stud backlog snapshot: total/heading/trim/plate quantities and lot counts. For 'backlog by X' or 'largest/top X in backlog' questions use backlog_breakdown instead.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backlog_breakdown",
            "description": "Open backlog grouped by a stud/request dimension — answers 'largest backlog by stud size', 'backlog by customer', etc. Returns top groups with lot counts and total quantities. Optional equality filters drill down (e.g. group_by=stud_length with stud_size=1/4-20).",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {
                        "type": "string",
                        "enum": [
                            "stud_size",
                            "stud_length",
                            "stud_material",
                            "stud_mat_code",
                            "stud_type",
                            "stud_flange",
                            "req_customer",
                            "req_header",
                            "req_status",
                            "trim_level",
                            "stud_id",
                        ],
                        "description": "Dimension to group the open backlog by.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max groups to return, largest first (default 10).",
                    },
                    "stud_size": {
                        "type": "string",
                        "description": "Filter: exact stud size, e.g. '1/4-20', '10-32', 'M6'.",
                    },
                    "stud_material": {"type": "string", "description": "Filter: exact stud material name."},
                    "stud_flange": {"type": "string", "description": "Filter: flange type."},
                    "stud_type": {"type": "string", "description": "Filter: stud type."},
                    "req_customer": {"type": "string", "description": "Filter: exact customer name."},
                    "req_header": {"type": "string", "description": "Filter: header machine name."},
                    "req_status": {"type": "string", "description": "Filter: request status/stage."},
                },
                "required": ["group_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backlog_lots",
            "description": "Individual open backlog lots with full stud dimensions (size, length, material, customer, status). Use when the user asks about specific lots rather than grouped totals. Same optional filters as backlog_breakdown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max lots to return (default 100)."},
                    "stud_size": {"type": "string", "description": "Filter: exact stud size, e.g. '1/4-20'."},
                    "stud_material": {"type": "string", "description": "Filter: exact stud material name."},
                    "req_customer": {"type": "string", "description": "Filter: exact customer name."},
                    "req_header": {"type": "string", "description": "Filter: header machine name."},
                    "req_status": {"type": "string", "description": "Filter: request status/stage."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heading_overall",
            "description": "Heading plan-vs-actual for the whole department: goal %, studs vs plan, rate vs plan, uptime, running header count. Use for general 'how is heading/production doing' questions.",
            "parameters": {
                "type": "object",
                "properties": {"data_date": _DATE_PARAM},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heading",
            "description": "Per-header heading daily metrics (studs, vs-plan %, efficiency, uptime per machine). Use only when a specific head or a per-head breakdown is requested.",
            "parameters": {
                "type": "object",
                "properties": {
                    "head_name": {
                        "type": "string",
                        "enum": [
                            "CARLO_SALVI",
                            "FENG_PEI_1",
                            "FENG_PEI_2",
                            "FENG_PEI_3",
                            "NATIONAL_1",
                            "NATIONAL_2",
                            "NATIONAL_3",
                            "SP11",
                            "SP21-1",
                            "SP21-2",
                        ],
                        "description": "Specific header machine. Omit for all headers.",
                    },
                    "data_date": _DATE_PARAM,
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "heading_summary",
            "description": "Heading stud totals for week-to-date, month-to-date, and year-to-date, plus day-over-day trend vs the previous business day. Use for 'studs this week/month/year' questions.",
            "parameters": {
                "type": "object",
                "properties": {"data_date": _DATE_PARAM},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales",
            "description": "Daily Sage sales & quotes: today's totals, order counts, top items, product mix, WTD/MTD/YTD rollups, and day-over-day trend.",
            "parameters": {
                "type": "object",
                "properties": {"data_date": _DATE_PARAM},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sage_trend",
            "description": "Per-day quotes and sales dollar series over the trailing N weekdays. Use for 'sales over the last week/month' or trend questions spanning multiple days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Trailing weekdays to include (default 30)."},
                    "end_date": {
                        "type": "string",
                        "description": "Optional ISO end date YYYY-MM-DD. Omit for the latest data.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipment_sold",
            "description": (
                "Equipment/machine UNITS sold over a period from Sage orders, broken down by "
                "model (QUICKSHOT, MODULAR, TITAN, ...). Units only — contains no dollar values. "
                "'Sold' = order booked in Sage, not shipped. Use for 'how many machines/Quickshots "
                "did we sell this week' and per-model breakdown follow-ups. Every response already "
                "includes the per-model rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {
                        "type": "string",
                        "enum": ["week", "month", "ytd"],
                        "description": "Period: week = Monday-today (default), month, or ytd. Ignored when explicit dates are given.",
                    },
                    "start_date": {"type": "string", "description": "Optional ISO range start YYYY-MM-DD."},
                    "end_date": {"type": "string", "description": "Optional ISO range end YYYY-MM-DD."},
                    "equip_type": {
                        "type": "string",
                        "enum": ["MACHINE", "GUN", "WELD HEAD", "FEEDER BOWL"],
                        "description": "Restrict to one equipment type. Use MACHINE for 'machines sold'. Omit for all equipment.",
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["model", "item", "type", "day"],
                        "description": "Breakdown dimension (default model).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipment_stock",
            "description": "Finished equipment ready in stock (built, owned by STOCK) by type/model/spec — machines, guns, weld heads, feeder bowls. Not wire inventory (use livewire_inventory for wire).",
            "parameters": {
                "type": "object",
                "properties": {
                    "equip_type": {
                        "type": "string",
                        "enum": ["MACHINE", "GUN", "WELD HEAD", "FEEDER BOWL"],
                        "description": "Optional type filter.",
                    },
                    "model": {"type": "string", "description": "Optional model filter, e.g. QUICKSHOT."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipment_builds",
            "description": "Open equipment build requests: customer, due date, stage (NEW/BUILDING/WAITING_ON_PARTS/QA/READY_TO_SHIP), completion %, and owed (not yet built) items per request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["OPEN", "BACKORDERED"],
                        "description": "Optional status filter (default: both).",
                    },
                    "stage": {
                        "type": "string",
                        "enum": ["NEW", "BUILDING", "WAITING_ON_PARTS", "QA", "READY_TO_SHIP"],
                        "description": "Optional stage filter.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipment_parts",
            "description": "Equipment BOM parts stock vs minimum — which machine-build parts are low or below reorder point. Not wire (use livewire tools for wire).",
            "parameters": {
                "type": "object",
                "properties": {
                    "low_only": {
                        "type": "boolean",
                        "description": "true (default) = only parts below minimum; false = full parts list.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipment_repairs",
            "description": "Open equipment repair tickets: customer, end user, machine/gun serial, stage, and dates received/shipped. Use for 'what repairs are open', 'what stage is X's repair in'. For finished/closed repairs use equipment_repairs_history instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stage": {
                        "type": "string",
                        "description": "Optional stage filter, e.g. RECEIVED, DIAGNOSED, REPAIRING.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equipment_repairs_history",
            "description": "Closed/completed equipment repair tickets, most recently received first. Use for 'what repairs have we done for X', past repair history by customer or date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "string", "description": "Optional exact customer name filter."},
                    "start_date": {
                        "type": "string",
                        "description": "Optional ISO range start YYYY-MM-DD, filters on date received.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional ISO range end YYYY-MM-DD, filters on date received.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return, 1-500 (default 100).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_runs",
            "description": "Data pipeline health: latest run per compute job with 24h success stats. Use when asked whether the data/jobs are up to date or failing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_name": {"type": "string", "description": "Optional single job to inspect."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "livewire_demand",
            "description": "4-week wire demand vs inventory by material and wire dia range; shortage flags. Numbers in the rows are authoritative — never estimate order quantities yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "material_query": {
                        "type": "string",
                        "description": "Case-insensitive substring filter on material code/name (e.g. 'mild steel', '302').",
                    },
                    "shortages_only": {
                        "type": "boolean",
                        "description": "Return only rows flagged as shortages.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "livewire_inventory",
            "description": "Current wire spool inventory levels grouped by location/material/code/dia/owner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "enum": ["SHED", "HEAD", "FARM"],
                        "description": "Optional location filter.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "livewire_usage",
            "description": "Wire consumption in lbs over 30/90/180/365 days, grouped by vendor, material, or wire diameter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["vendor", "material", "wire_dia"],
                        "description": "Grouping dimension. Omit for the default grouping.",
                    },
                },
                "required": [],
            },
        },
    },
]

_MAX_CALLS_PER_ROUND = 8


def run_tool_loop(
    llm: Any,
    call_api: Callable[[dict[str, Any]], dict[str, Any] | None],
    trim_evidence: Callable[[str, dict[str, Any]], dict[str, Any]],
    system_prompt: str,
    history: list[dict[str, str]],
    user_query: str,
    max_rounds: int = 3,
    tools: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
) -> tuple[str, str | None, list[dict[str, Any]]]:
    """Drive tool-call rounds until the model produces a final answer.

    Returns (answer, model_used, tools_used). Raises on any failure — the
    orchestrator falls back to the legacy single-endpoint path. `tools` lets
    the caller offer a restricted subset (e.g. per-user access control); only
    offered tools are ever executed, even if the model requests another name.
    `evidence`, when given, receives each successful tool result keyed by
    tool name ("heading", then "heading#2" for a repeat) for the answer renderer.
    """
    if tools is None:
        tools = TOOL_DEFS
    allowed_names = {tool["function"]["name"] for tool in tools}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "user", "content": user_query},
    ]
    tools_used: list[dict[str, Any]] = []
    model_used: str | None = None

    for round_no in range(1, max_rounds + 1):
        # Last round: force a text answer instead of more tool calls.
        tool_choice = "none" if round_no == max_rounds and tools_used else None
        message, model_used = llm.complete_with_tools(messages, tools, tool_choice=tool_choice)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            answer = (message.get("content") or "").strip()
            if not answer:
                raise RuntimeError("tool loop returned neither content nor tool calls")
            return answer, model_used, tools_used

        messages.append(
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        # Every call id in the assistant message needs a tool response, or the
        # next request 400s ("No tool output found for function call ...").
        # Calls past the cap are answered as skipped instead of dropped.
        for index, call in enumerate(tool_calls):
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if index >= _MAX_CALLS_PER_ROUND:
                logger.info("Tool router skipped %s: over %s calls this round", name, _MAX_CALLS_PER_ROUND)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": name,
                        "content": json.dumps(
                            {
                                "error": f"skipped: at most {_MAX_CALLS_PER_ROUND} tool calls per round. "
                                "Call it again next round if you still need it."
                            }
                        ),
                    }
                )
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}

            # Never execute a tool that wasn't offered (access control).
            data = call_api({"endpoint": name, "parameters": arguments}) if name in allowed_names else None
            tools_used.append({"tool": name, "arguments": arguments, "ok": data is not None})
            logger.info("Tool router called %s(%s) -> %s", name, arguments, "ok" if data is not None else "no data")
            if data is None:
                result_text = json.dumps(
                    {"error": f"{name} returned no data (unknown tool, unavailable, or no rows for those parameters)"}
                )
            else:
                result_text = json.dumps(trim_evidence(name, data), default=str)
                if evidence is not None:
                    key, n = name, 1
                    while key in evidence:
                        n += 1
                        key = f"{name}#{n}"
                    evidence[key] = data
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "name": name,
                    "content": result_text,
                }
            )

    raise RuntimeError(f"tool loop hit the {max_rounds}-round cap without a final answer")
