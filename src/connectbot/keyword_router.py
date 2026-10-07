"""Deterministic keyword routing: the vocabulary that classifies a Slack question
and the ordered regex rules that map it to one FastAPI endpoint without an LLM.

Rule order in resolve_keyword_intent matters — see tests/test_keyword_routing.py.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

_BUSINESS_KEYWORDS = {
    "iwt",
    "summary",
    "status",
    "brief",
    "operations",
    "backlog",
    "bottleneck",
    "heading",
    "header",
    "trimmer",
    "quality",
    "adjustment",
    "adjustments",
    "lot",
    "lots",
    "mfgreq",
    "manufacturing",
    "lead time",
    "eta",
    "stud",
    "repair",
    "repairs",
    "equipment",
    "wire",
    "livewire",
    "material",
    "mild steel",
    "steel",
    "order",
    "shortage",
    "inventory",
    "plating",
    "andon",
    "sales",
    "revenue",
    "quote",
    "quotes",
    "sold",
    "sage",
    "machine",
    "machines",
    "quickshot",
    "modular",
    "titan",
    "fusion",
    "parts",
    "build",
    "builds",
    "rundown",
    "overview",
    "report",
    "full picture",
    "big picture",
    "day going",
}

_FASTAPI_CONNECTIVITY_KEYWORDS = {
    "connectfastapi",
    "connect fastapi",
    "fastapi",
    "api",
}

_FOLLOWUP_HINTS = {
    "and",
    "also",
    "what about",
    "how about",
    "that one",
    "same",
    "next",
    "more",
    "details",
    "breakdown",
    "break down",
    "break it down",
    "split",
    "by model",
    "which models",
}

# Equipment nouns (machines, models from EQUIP/equipmentConfig.json, guns,
# weld heads, feeder bowls). Paired with sold/stock verbs these route to the
# open equipment_* endpoints instead of the gated dollar 'sales' endpoint.
_EQUIPMENT_NOUN_RE = re.compile(
    r"\b(machines?|equipment|quickshots?|modulars?|titan(?:\s+gfx)?|"
    r"fusion(?:\s+(?:600|1000))?|atlas|freedom|liberty|"
    r"weld\s*heads?|feeder\s*bowls?|guns?)\b"
)

_GREETING_TERMS = {
    "hi",
    "hello",
    "hey",
    "good morning",
    "good afternoon",
    "good evening",
    "yo",
}

_THANKS_TERMS = {
    "thanks",
    "thank you",
    "thx",
    "appreciate it",
}

_CAPABILITIES_HINTS = {
    "what can you do",
    "what do you do",
    "what else can you do",
    "what are you capable of",
    "what are your capabilities",
    "tell me what you can do",
    "what do you know",
    "what do you support",
    "what topics",
    "what questions",
    "what can you help",
    "help me with",
    "able to do",
    "what are you able",
    "capable of",
    "what can i ask",
    "what should i ask",
    "what all can you",
    "what kind of things",
    "what kinds of things",
    "what sort of things",
    "what data do you",
    "what do you have access",
    "what apis",
    "which apis",
    "what endpoints",
    "list your",
    "your features",
}


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _extract_data_date(normalized: str) -> str | None:
    """Pull an explicit date reference out of a query as ISO YYYY-MM-DD.

    Handles "yesterday", "N days ago", "last friday", and "on 7/14" (slash
    dates only — hyphens collide with head names like SP21-1). Returns None
    when the query means "now/today", which is every endpoint's default.
    """
    today = date.today()

    if "yesterday" in normalized:
        return (today - timedelta(days=1)).isoformat()

    match = re.search(r"\b(\d+)\s+days?\s+ago\b", normalized)
    if match:
        return (today - timedelta(days=int(match.group(1)))).isoformat()

    match = re.search(
        r"\b(?:last|this past|on)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        normalized,
    )
    if match:
        delta = (today.weekday() - _WEEKDAYS.index(match.group(1))) % 7 or 7
        return (today - timedelta(days=delta)).isoformat()

    match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", normalized)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None
        if not match.group(3) and parsed > today:
            parsed = date(year - 1, month, day)
        return parsed.isoformat()

    return None


def _has_term(text: str, terms: set[str]) -> bool:
    """Word-boundary match so 'yo' doesn't match inside 'you'."""
    return any(re.search(r"(?:^|\b)" + re.escape(term) + r"(?:\b|$)", text) for term in terms)


# Phrases that mean "give me the whole picture" — the tool router composes
# these from several endpoints (heading_overall + sales + backlog, etc.).
_RUNDOWN_TERMS = (
    "rundown",
    "run down",
    "morning report",
    "daily report",
    "how's the day",
    "hows the day",
    "day going",
    "full picture",
    "big picture",
    "summary",
    "overview",
    "brief",
)

# Distinct data topics; naming two or more in one question means a single
# endpoint can't answer it, so it goes to the tool router.
_TOPIC_GROUPS = {
    "heading": ("heading", "header"),
    "sales": ("sales", "revenue", "quote", "sold", "sage"),
    "backlog": ("backlog", "bottleneck"),
    "wire": ("wire", "livewire", "spool", "shortage"),
    "equipment": ("machine", "equipment", "quickshot", "modular", "titan", "fusion", "build"),
}

# Data domains whose compute jobs are being rebuilt/verified one at a time.
# Their endpoints were removed from connectFastAPI; answer honestly instead
# of making a doomed API call or giving a vague failure.
_RETIRED_TOPICS = {
    "trimmers": ("trimmer", "trimmers"),
    "manufacturing lots": ("lot", "lots", "mfgreq", "manufacturing request"),
    "adjustments": ("adjustment", "adjustments"),
    "data quality": ("data quality", "quality"),
    "ETA / lead time": ("eta", "lead time"),
    "manufacturing summary": ("summary", "brief", "overview"),
    "backlog breakdown": (),  # reachable only via old LLM habits, not keywords
}


def resolve_keyword_intent(user_query: str) -> dict[str, Any] | None:
    """Fast deterministic intent resolution from keywords — no LLM needed."""
    normalized = re.sub(r"\s+", " ", user_query.strip().lower())
    params: dict[str, Any] = {}
    data_date = _extract_data_date(normalized)

    def _dated(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(extra or {})
        if data_date:
            merged["data_date"] = data_date
        return merged

    def _material_hint() -> str | None:
        for mat in ("mild steel", "stainless", "aluminum", "aluminium", "brass", "1010-ms", "302", "304", "316"):
            if mat in normalized:
                return "aluminum" if mat == "aluminium" else mat
        return None

    # --- equipment units/stock/builds/parts — MUST run before the sales
    # rule: "\bsold\b" below would otherwise send "machines sold" to the
    # GATED dollar endpoint; equipment units are open to everyone. ---
    equip_noun = _EQUIPMENT_NOUN_RE.search(normalized)

    def _equip_type_and_model(noun: str) -> tuple[str | None, str | None]:
        noun = re.sub(r"\s+", " ", noun.strip())
        if noun.startswith("gun"):
            return "GUN", None
        if noun.startswith("weld"):
            return "WELD HEAD", None
        if noun.startswith("feeder"):
            return "FEEDER BOWL", None
        if noun.startswith("machine") or noun == "equipment":
            return ("MACHINE", None) if noun.startswith("machine") else (None, None)
        # a specific machine model name (quickshots -> QUICKSHOT, etc.)
        model = noun.upper().rstrip("S") if noun in ("quickshots", "modulars") else noun.upper()
        return "MACHINE", model

    if equip_noun and re.search(r"\b(sold|sell|selling|sales?)\b", normalized):
        equip_type, model = _equip_type_and_model(equip_noun.group(1))
        eq_params: dict[str, Any] = {"group_by": "model"}
        if re.search(r"\b(month|mtd)\b", normalized):
            eq_params["window"] = "month"
        elif re.search(r"\b(year|ytd|annual)\b", normalized):
            eq_params["window"] = "ytd"
        else:
            eq_params["window"] = "week"
        if data_date:
            eq_params["start_date"] = data_date
            eq_params["end_date"] = data_date
        if equip_type:
            eq_params["equip_type"] = equip_type
        return {
            "endpoint": "equipment_sold",
            "parameters": eq_params,
            "reasoning": "keyword: equipment noun + sold/sell",
            "confidence": 0.9,
        }

    if equip_noun and re.search(r"\b(in stock|on hand|stock|ready|available)\b", normalized):
        equip_type, model = _equip_type_and_model(equip_noun.group(1))
        if equip_type:
            params["equip_type"] = equip_type
        if model:
            params["model"] = model
        return {
            "endpoint": "equipment_stock",
            "parameters": params,
            "reasoning": "keyword: equipment noun + stock/on hand",
            "confidence": 0.85,
        }

    if re.search(
        r"\b(open builds?|build pipeline|builds? in progress|being built|build requests?|equipment requests?)\b",
        normalized,
    ) or (
        re.search(r"\bbuilds?\b", normalized)
        and (equip_noun or re.search(r"\b(open|progress|pipeline|status|going|outstanding)\b", normalized))
    ):
        return {
            "endpoint": "equipment_builds",
            "parameters": {},
            "reasoning": "keyword: equipment builds",
            "confidence": 0.85,
        }

    if re.search(r"\bparts?\b", normalized) and re.search(
        r"\b(low|min|minimum|reorder|stock|below|running out|need|order)\b", normalized
    ):
        if re.search(r"\ball parts\b|\bfull (parts )?list\b", normalized):
            params["low_only"] = False
        return {
            "endpoint": "equipment_parts",
            "parameters": params,
            "reasoning": "keyword: parts + stock/low/reorder",
            "confidence": 0.8,
        }

    # --- equipment repairs: open tickets vs closed/history ---
    if re.search(r"\brepairs?\b", normalized):
        if re.search(r"\b(closed|complete|completed|finished|done|history|past|shipped)\b", normalized):
            return {
                "endpoint": "equipment_repairs_history",
                "parameters": {},
                "reasoning": "keyword: repairs + closed/history",
                "confidence": 0.85,
            }
        stage_params: dict[str, Any] = {}
        if re.search(r"\breceived\b", normalized):
            stage_params["stage"] = "RECEIVED"
        elif re.search(r"\bdiagnos", normalized):
            stage_params["stage"] = "DIAGNOSED"
        elif re.search(r"\brepairing\b|\bin repair\b|\bpending\b", normalized):
            stage_params["stage"] = "REPAIRING"
        return {
            "endpoint": "equipment_repairs",
            "parameters": stage_params,
            "reasoning": "keyword: repairs (open)",
            "confidence": 0.85,
        }

    # --- sales / quotes (Sage) — before generic order/summary rules ---
    if re.search(r"\bsales?\b|\brevenue\b|\bquotes?\b|\bsold\b|\binvoices?\b|\bsage\b", normalized):
        trend_terms = (
            "trend",
            "over the past",
            "over the last",
            "past week",
            "last week",
            "past month",
            "last month",
            "history",
            "chart",
            "series",
            "per day",
            "each day",
            "daily breakdown",
        )
        if any(term in normalized for term in trend_terms):
            # Multi-day series lives in sage_trend — tool router picks days.
            return None
        return {
            "endpoint": "sales",
            "parameters": _dated(),
            "reasoning": "keyword: sales/quotes/revenue",
            "confidence": 0.85,
        }

    # --- livewire inventory (stock on hand) ---
    if (
        "inventory" in normalized
        or "in stock" in normalized
        or "on hand" in normalized
        or (
            ("spool" in normalized or "shed" in normalized or "farm" in normalized)
            and "shortage" not in normalized
            and "order" not in normalized
        )
    ):
        for loc in ("shed", "head", "farm"):
            if loc in normalized:
                params["location"] = loc.upper()
                break
        return {
            "endpoint": "livewire_inventory",
            "parameters": params,
            "reasoning": "keyword: inventory/stock/spool",
            "confidence": 0.85,
        }

    # --- livewire usage (consumption over time) ---
    if any(kw in normalized for kw in ("usage", "consumption", "consumed", "used", "using")) and (
        "wire" in normalized or "material" in normalized or "livewire" in normalized or "vendor" in normalized
    ):
        if "vendor" in normalized:
            params["dimension"] = "vendor"
        elif "dia" in normalized or "diameter" in normalized:
            params["dimension"] = "wire_dia"
        elif "material" in normalized:
            params["dimension"] = "material"
        return {
            "endpoint": "livewire_usage",
            "parameters": params,
            "reasoning": "keyword: usage/consumption",
            "confidence": 0.85,
        }

    # --- livewire demand / shortages / ordering ---
    if any(kw in normalized for kw in ("wire", "livewire", "shortage", "mild steel", "demand")):
        mat = _material_hint()
        if mat:
            params["material_query"] = mat
        if "shortage" in normalized or "short" in normalized:
            params["shortages_only"] = True
        return {
            "endpoint": "livewire_demand",
            "parameters": params,
            "reasoning": "keyword: wire/livewire/shortage/demand",
            "confidence": 0.85,
        }

    # --- retired data domains: answer honestly, no doomed API call ---
    # Word-boundary match: "eta" must not fire inside "detail", etc.
    for topic, keywords in _RETIRED_TOPICS.items():
        if keywords and any(re.search(r"\b" + re.escape(kw) + r"\b", normalized) for kw in keywords):
            return {
                "endpoint": "retired",
                "parameters": {"topic": topic},
                "reasoning": f"keyword: retired topic {topic}",
                "confidence": 0.9,
            }

    # --- backlog ---
    if "backlog" in normalized or "bottleneck" in normalized:
        drill_terms = (
            "by ",
            "per ",
            "size",
            "length",
            "material",
            "customer",
            "largest",
            "biggest",
            "top ",
            "breakdown",
            "break down",
            "which",
            "flange",
            "type",
            "lot",
            "header",
        )
        if any(term in normalized for term in drill_terms):
            # Grouped/filtered backlog questions need backlog_breakdown or
            # backlog_lots — let the tool router pick dimension and filters.
            return None
        return {"endpoint": "backlog", "parameters": {}, "reasoning": "keyword: backlog", "confidence": 0.85}

    # --- heading: overall plan-vs-actual by default; per-head rows only when asked ---
    if "heading" in normalized or "header" in normalized:
        head_match = re.search(
            r"\b(carlo[\s_-]*salvi|salvi|feng[\s_-]*pei[\s_-]*([123])?|national[\s_-]*([123])?|sp[\s_-]*11|sp[\s_-]*21[\s_-]*([12])?)\b",
            normalized,
        )
        if head_match:
            raw = head_match.group(1)
            if "salvi" in raw:
                params["head_name"] = "CARLO_SALVI"
            elif "feng" in raw and head_match.group(2):
                params["head_name"] = f"FENG_PEI_{head_match.group(2)}"
            elif "national" in raw and head_match.group(3):
                params["head_name"] = f"NATIONAL_{head_match.group(3)}"
            elif "21" in raw and head_match.group(4):
                params["head_name"] = f"SP21-{head_match.group(4)}"
            elif "11" in raw:
                params["head_name"] = "SP11"
            # A head family without a number (e.g. "feng pei") falls through
            # with no head_name — full per-head rows let the LLM answer it.
            return {
                "endpoint": "heading",
                "parameters": _dated(params),
                "reasoning": "keyword: specific head",
                "confidence": 0.85,
            }
        breakdown_terms = (
            "by head",
            "per head",
            "each head",
            "which head",
            "every head",
            "by header",
            "per header",
            "each header",
            "which header",
            "breakdown",
            "break down",
            "all heads",
            "all headers",
            "individual",
        )
        if any(term in normalized for term in breakdown_terms):
            return {
                "endpoint": "heading",
                "parameters": _dated(),
                "reasoning": "keyword: heading per-head breakdown",
                "confidence": 0.85,
            }
        return {
            "endpoint": "heading_overall",
            "parameters": _dated(),
            "reasoning": "keyword: heading status (overall plan-vs-actual)",
            "confidence": 0.85,
        }

    # --- material / order (generic) → demand ---
    if any(kw in normalized for kw in ("material", "order")):
        mat = _material_hint()
        if mat:
            params["material_query"] = mat
        return {
            "endpoint": "livewire_demand",
            "parameters": params,
            "reasoning": "keyword: material/order",
            "confidence": 0.70,
        }

    return None
