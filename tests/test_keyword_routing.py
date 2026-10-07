"""Deterministic keyword routing: query -> (endpoint, parameters).

Rule order in _resolve_keyword_intent matters (equipment before sales, retired
topics before backlog/heading). Most rows are real questions from
logs/connectbot.log. `None` means "no keyword rule" — the question goes on to
the follow-up resolver / tool router.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.connectbot.orchestrator import ConnectBotOrchestrator

YESTERDAY = "2026-10-06"  # frozen today is 2026-10-07

ROUTES: list[tuple[str, str | None, dict[str, Any]]] = [
    # --- equipment (must win over the gated dollar 'sales' rule) ---
    ("how many equipment have we sold this week?", "equipment_sold", {"group_by": "model", "window": "week"}),
    ("give me a full breakdown of all equipment sales ytd", "equipment_sold", {"group_by": "model", "window": "ytd"}),
    (
        "how many modulars have we sold this year",
        "equipment_sold",
        {"group_by": "model", "window": "ytd", "equip_type": "MACHINE"},
    ),
    ("machines sold this month", "equipment_sold", {"group_by": "model", "window": "month", "equip_type": "MACHINE"}),
    (
        "guns sold yesterday",
        "equipment_sold",
        {"group_by": "model", "window": "week", "start_date": YESTERDAY, "end_date": YESTERDAY, "equip_type": "GUN"},
    ),
    ("machines in stock", "equipment_stock", {"equip_type": "MACHINE"}),
    ("quickshots ready to ship", "equipment_stock", {"equip_type": "MACHINE", "model": "QUICKSHOT"}),
    ("weld heads on hand", "equipment_stock", {"equip_type": "WELD HEAD"}),
    ("what open builds do we have", "equipment_builds", {}),
    ("which parts are running low", "equipment_parts", {}),
    ("show me all parts below minimum full parts list", "equipment_parts", {"low_only": False}),
    ("any open repairs?", "equipment_repairs", {}),
    ("repairs received this week", "equipment_repairs", {"stage": "RECEIVED"}),
    ("what repairs are being diagnosed", "equipment_repairs", {"stage": "DIAGNOSED"}),
    ("closed repairs for acme", "equipment_repairs_history", {}),
    # --- sales (gated) ---
    ("can you give me the sales data for today so far?", "sales", {}),
    ("hmm, whats the week to date sales?", "sales", {}),
    ("how do quotes compare this week versus sales?", "sales", {}),
    ("sales yesterday", "sales", {"data_date": YESTERDAY}),
    ("sales trend over the last month", None, {}),  # multi-day series -> tool router (sage_trend)
    ("what is our top selling item?", None, {}),
    # --- livewire ---
    ("can you tell me how much 302 we have in stock?", "livewire_inventory", {}),
    ("spool inventory in the shed", "livewire_inventory", {"location": "SHED"}),
    ("wire usage by vendor", "livewire_usage", {"dimension": "vendor"}),
    ("wire consumption by diameter", "livewire_usage", {"dimension": "wire_dia"}),
    ("what has our 1010-MS usage looked like lately?", None, {}),
    ("can you give me todays demand snapshot please", "livewire_demand", {}),
    ("please show me current livewire shortages", "livewire_demand", {"shortages_only": True}),
    ("give me the livewire demand shortages please", "livewire_demand", {"shortages_only": True}),
    ("what wire is low?", "livewire_demand", {}),
    ("can you check what wire we should order?", "livewire_demand", {}),  # "should" is not "short"
    ("how much mild steel should i order?", "livewire_demand", {"material_query": "mild steel"}),
    ("how much 1010-ms should i order?", "livewire_demand", {"material_query": "1010-ms"}),
    ("what aluminum do i need to order?", "livewire_demand", {"material_query": "aluminum"}),
    ("how much 0.212 stainless steel do we have?", None, {}),
    # --- retired data domains ---
    ("trimmer metrics", "retired", {"topic": "trimmers"}),
    ("what's the lead time for 500 studs", "retired", {"topic": "ETA / lead time"}),
    ("show me mfgreq lots", "retired", {"topic": "manufacturing lots"}),
    ("can you give me more detail", None, {}),  # "eta" must not match inside "detail"
    # --- backlog ---
    ("can you tell me our backlog", "backlog", {}),
    ("whats the backlog today", "backlog", {}),
    ("nice, can you tell me our heading backlog as of today?", "backlog", {}),
    ("can you tell me the largest stud backlog by stud size right now?", None, {}),  # drill-down -> tool router
    ("great, now can you give me the breakdown of our backlog in terms of the top stud sizes?", None, {}),
    # --- heading ---
    ("hows heading going today", "heading_overall", {}),
    ("great, whats the heading plan look like today?", "heading_overall", {}),
    ("whats up with the headers", "heading_overall", {}),
    ("heading yesterday", "heading_overall", {"data_date": YESTERDAY}),
    ("heading by head", "heading", {}),
    ("heading for sp21-1", "heading", {"head_name": "SP21-1"}),
    ("how is feng pei 2 heading", "heading", {"head_name": "FENG_PEI_2"}),
    ("carlo salvi heading today", "heading", {"head_name": "CARLO_SALVI"}),
    ("how is national heading", "heading", {}),  # family without a number -> all heads
    ("hows the carlo salvi doing today?", None, {}),  # no heading/header word
    # --- nothing matches ---
    ("how many machines have we made this year", None, {}),
    ("how many quickshots", None, {}),
    ("whats the weather today", None, {}),
]


@pytest.mark.parametrize(("query", "endpoint", "params"), ROUTES, ids=[r[0] for r in ROUTES])
def test_keyword_route(orch: ConnectBotOrchestrator, query: str, endpoint: str | None, params: dict[str, Any]) -> None:
    intent = orch._resolve_keyword_intent(query)
    if endpoint is None:
        assert intent is None
    else:
        assert intent is not None
        assert (intent["endpoint"], intent["parameters"]) == (endpoint, params)
