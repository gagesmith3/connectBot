"""
LLM-powered intent parser for natural language queries
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .llm_client import LLMClient
from .openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)


class IntentParser:
    """Parse user queries to determine which API to call and with what parameters"""

    SYSTEM_PROMPT = """You are an assistant that parses user queries about manufacturing business operations.

Given a user query, determine which API endpoint to call and what parameters to use.

Available API endpoints:
1. backlog - Get current stud backlog snapshot (total/heading/trim/plate quantities and lot counts)
2. heading - Get per-header heading daily metrics (head_name optional, data_date optional)
3. heading_overall - Get plan-vs-actual volume, rate, and utilization aggregated across all headers (data_date optional)
4. livewire_demand - Get 4-week wire demand vs inventory by material and wire dia range (material_query optional, shortages_only optional boolean)
5. livewire_inventory - Get current spool inventory levels per wire grouped by location/material/code/dia/owner (location optional: SHED, HEAD, or FARM)
6. livewire_usage - Get wire consumption in lbs over 30/90/180/365 days (dimension optional: vendor, material, or wire_dia)
7. sales - Get daily Sage sales & quotes: today's totals, top items, product mix, WTD/MTD/YTD rollups, and day-over-day trend (data_date optional)
8. backlog_breakdown - Open backlog grouped by a dimension (group_by required: stud_size, stud_length, stud_material, stud_mat_code, stud_type, stud_flange, req_customer, req_header, req_status, trim_level, stud_id; limit optional; equality filters optional e.g. stud_size)
9. backlog_lots - Per-lot open backlog rows with stud dimensions (limit optional, same filters as backlog_breakdown)
10. heading_summary - WTD/MTD/YTD stud totals plus day-over-day trend (data_date optional)
11. sage_trend - Per-day quotes/sales dollar series over trailing N weekdays (days optional, end_date optional)
12. compute_runs - Compute job health: latest run per job with 24h stats (job_name optional)
13. equipment_sold - Equipment/machine UNITS sold by model from Sage orders, no dollars (window optional: week, month, ytd; start_date/end_date optional; equip_type optional: MACHINE, GUN, WELD HEAD, FEEDER BOWL; group_by optional: model, item, type, day)
14. equipment_stock - Finished equipment ready in stock by type/model/spec (equip_type optional, model optional)
15. equipment_builds - Open equipment build requests with stage, completion %, owed items (status optional: OPEN, BACKORDERED; stage optional)
16. equipment_parts - Equipment BOM parts stock vs minimum, low-stock list (low_only optional boolean)
17. equipment_repairs - Open equipment repair tickets: customer, serial, stage, dates (stage optional)
18. equipment_repairs_history - Closed/completed equipment repair tickets, most recent first (customer optional, start_date/end_date optional, limit optional)

Retired data sources (do NOT map to these; respond with endpoint null): trimmers, manufacturing lots, adjustments, data quality, ETA/lead-time estimates, manufacturing summary.

Respond with ONLY valid JSON (no markdown, no backticks) with this structure:
{
    "endpoint": "endpoint_name",
    "parameters": {
        "param_name": "value"
    },
    "reasoning": "why this endpoint was chosen"
}

Examples:
- "What's our current backlog?" → {"endpoint": "backlog", "parameters": {}, "reasoning": "User asking for backlog status"}
- "Analyze our bottleneck" → {"endpoint": "backlog", "parameters": {}, "reasoning": "Backlog analysis helps identify bottlenecks"}
- "Show me heading data for NH5-1" → {"endpoint": "heading", "parameters": {"head_name": "NH5-1"}, "reasoning": "User asking for a specific header"}
- "How are we doing against the heading plan today?" → {"endpoint": "heading_overall", "parameters": {}, "reasoning": "User asking for plan-vs-actual heading performance"}
- "How much mild steel should I order?" → {"endpoint": "livewire_demand", "parameters": {"material_query": "mild steel"}, "reasoning": "User asking for wire demand/shortage guidance"}
- "What wire shortages do we have?" → {"endpoint": "livewire_demand", "parameters": {"shortages_only": true}, "reasoning": "User asking for shortage rows only"}
- "How much stainless is in the shed?" → {"endpoint": "livewire_inventory", "parameters": {"location": "SHED"}, "reasoning": "User asking for on-hand wire inventory"}
- "What's our aluminum usage lately?" → {"endpoint": "livewire_usage", "parameters": {"dimension": "material"}, "reasoning": "User asking for wire consumption trends"}
- "What's sales look like today?" → {"endpoint": "sales", "parameters": {}, "reasoning": "User asking for daily sales performance"}
- "How many quotes went out this week?" → {"endpoint": "sales", "parameters": {}, "reasoning": "User asking for quote activity"}
- "What's the largest stud backlog by stud size?" → {"endpoint": "backlog_breakdown", "parameters": {"group_by": "stud_size"}, "reasoning": "User asking for backlog grouped by stud size"}
- "How many studs have we made this month?" → {"endpoint": "heading_summary", "parameters": {}, "reasoning": "User asking for MTD stud totals"}
- "Show sales over the last two weeks" → {"endpoint": "sage_trend", "parameters": {"days": 10}, "reasoning": "User asking for a multi-day sales series"}
- "How many machines have we sold this week?" → {"endpoint": "equipment_sold", "parameters": {"equip_type": "MACHINE"}, "reasoning": "User asking for machine unit counts, not dollars"}
- "What machines are in stock?" → {"endpoint": "equipment_stock", "parameters": {"equip_type": "MACHINE"}, "reasoning": "User asking for finished equipment on hand"}
- "What builds are open?" → {"endpoint": "equipment_builds", "parameters": {}, "reasoning": "User asking for open equipment build requests"}
- "Any build parts running low?" → {"endpoint": "equipment_parts", "parameters": {}, "reasoning": "User asking for low-stock equipment parts"}
- "What repairs do we have open?" → {"endpoint": "equipment_repairs", "parameters": {}, "reasoning": "User asking for open equipment repair tickets"}
- "What repairs have we done for Acme?" → {"endpoint": "equipment_repairs_history", "parameters": {"customer": "Acme"}, "reasoning": "User asking for closed repair history for a customer"}

If the query is ambiguous but business-related, default to backlog.
If a query cannot be answered with available endpoints, respond with:
{"endpoint": null, "parameters": {}, "reasoning": "Cannot process this query with available endpoints"}"""

    _SMALL_TALK_KEYWORDS = {
        "hello",
        "hi",
        "hey",
        "thanks",
        "thank",
        "morning",
        "afternoon",
        "evening",
        "how are you",
        "what can you do",
        "who are you",
        "joke",
        "help",
    }

    _SUPPORTED_ENDPOINTS = {
        "backlog",
        "backlog_breakdown",
        "backlog_lots",
        "heading",
        "heading_overall",
        "heading_summary",
        "livewire_demand",
        "livewire_inventory",
        "livewire_usage",
        "sales",
        "sage_trend",
        "equipment_sold",
        "equipment_stock",
        "equipment_builds",
        "equipment_parts",
        "equipment_repairs",
        "equipment_repairs_history",
        "compute_runs",
        "fastapi_healthcheck",
        "retired",
        "help",
        "chat",
    }

    def _score_intent(self, query: str, endpoint: str | None, params: dict[str, Any]) -> float:
        normalized = query.strip().lower()

        if endpoint in {"help", "chat", "retired"}:
            return 0.95
        if endpoint not in self._SUPPORTED_ENDPOINTS:
            return 0.0

        score = 0.55

        if endpoint in {"backlog", "backlog_breakdown", "backlog_lots"} and (
            "backlog" in normalized or "bottleneck" in normalized
        ):
            score += 0.25

        if endpoint in {"heading", "heading_overall", "heading_summary"} and (
            "heading" in normalized or "header" in normalized or "plan" in normalized or "stud" in normalized
        ):
            score += 0.25

        if endpoint == "livewire_demand" and (
            "livewire" in normalized
            or "wire" in normalized
            or "mild steel" in normalized
            or "material" in normalized
            or "order" in normalized
            or "shortage" in normalized
            or "demand" in normalized
        ):
            score += 0.30

        if endpoint == "livewire_inventory" and (
            "inventory" in normalized or "stock" in normalized or "spool" in normalized or "shed" in normalized
        ):
            score += 0.30

        if endpoint == "livewire_usage" and (
            "usage" in normalized or "consumption" in normalized or "consumed" in normalized or "used" in normalized
        ):
            score += 0.30

        if endpoint in {"sales", "sage_trend"} and (
            "sales" in normalized
            or "sale" in normalized
            or "revenue" in normalized
            or "quote" in normalized
            or "sold" in normalized
            or "sage" in normalized
        ):
            score += 0.30

        if endpoint in {"equipment_sold", "equipment_stock", "equipment_builds", "equipment_parts"} and (
            "machine" in normalized
            or "equipment" in normalized
            or "quickshot" in normalized
            or "modular" in normalized
            or "titan" in normalized
            or "fusion" in normalized
            or "gun" in normalized
            or "build" in normalized
            or "part" in normalized
        ):
            score += 0.30

        if len(normalized.split()) <= 2 and endpoint not in {"help", "chat"}:
            score -= 0.1

        return max(0.0, min(0.99, score))

    def _enrich_intent(self, query: str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = payload.get("endpoint")
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            parameters = {}

        confidence = self._score_intent(query, endpoint, parameters)

        return {
            "endpoint": endpoint,
            "parameters": parameters,
            "reasoning": payload.get("reasoning", "No reasoning provided"),
            "confidence": confidence,
            "missing_params": [],
        }

    def __init__(
        self,
        openrouter_api_key: str,
        models: list[str],
        base_url: str,
        site_url: str = "",
        site_name: str = "Connect Bot",
        timeout_seconds: float = 20.0,
        llm_client: LLMClient | None = None,
    ):
        self.openrouter_api_key = openrouter_api_key
        self.models = models
        if llm_client is not None:
            self._client = llm_client
        else:
            self._client = OpenRouterClient(
                api_key=openrouter_api_key,
                models=models,
                base_url=base_url,
                site_url=site_url,
                site_name=site_name,
                timeout_seconds=timeout_seconds,
            )

    def _create_completion(self, query: str) -> str:
        content, _ = self._client.complete(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ]
        )
        return content

    def _fallback_parse(self, query: str) -> dict[str, Any]:
        normalized = " ".join(query.strip().lower().split())

        if (
            ("connectfastapi" in normalized or "connect fastapi" in normalized or "fastapi" in normalized)
            and any(
                token in normalized
                for token in {"connected", "connection", "online", "up", "down", "available", "health", "status", "reachable"}
            )
        ):
            return {
                "endpoint": "fastapi_healthcheck",
                "parameters": {},
                "reasoning": "FastAPI connectivity status check requested",
            }

        if normalized in {"test", "testing", "ping"}:
            return {
                "endpoint": "chat",
                "parameters": {
                    "message": "I’m up and responding. Live data calls are separate, so if FastAPI is unavailable I can still handle simple chat and help prompts."
                },
                "reasoning": "Basic connectivity check handled locally",
            }

        if normalized in {"hi", "hello", "hey", "help", "what can you do?", "what do you do?"}:
            return {
                "endpoint": "help",
                "parameters": {},
                "reasoning": "Greeting or help request handled locally",
            }

        if normalized in {"thanks", "thank you", "thx", "cool", "nice"}:
            return {
                "endpoint": "chat",
                "parameters": {
                    "message": "You can ask me for backlog, heading metrics, wire inventory, wire usage, or wire demand. If live data is down, I’ll tell you that directly."
                },
                "reasoning": "Basic conversational acknowledgement handled locally",
            }

        if normalized in {"who are you", "what are you", "how are you"}:
            return {
                "endpoint": "chat",
                "parameters": {
                    "message": "I’m ConnectBot. I can handle simple chat locally and use FastAPI for live manufacturing and operations data."
                },
                "reasoning": "Basic identity conversation handled locally",
            }

        if "backlog" in normalized or "bottleneck" in normalized:
            return {
                "endpoint": "backlog",
                "parameters": {},
                "reasoning": "Backlog-related query handled by keyword matching",
            }

        # Equipment units/stock must win before the sales rule — "machines
        # sold" is a units question for the open equipment endpoint, not the
        # gated dollar endpoint.
        equipment_noun = re.search(
            r"\b(machines?|equipment|quickshots?|modulars?|titan|fusion|weld\s*heads?|feeder\s*bowls?|guns?)\b",
            normalized,
        )
        if equipment_noun and re.search(r"\b(sold|sell|selling|sales?)\b", normalized):
            return {
                "endpoint": "equipment_sold",
                "parameters": {},
                "reasoning": "Equipment units sold query handled by keyword matching",
            }
        if equipment_noun and re.search(r"\b(in stock|on hand|stock|ready|available)\b", normalized):
            return {
                "endpoint": "equipment_stock",
                "parameters": {},
                "reasoning": "Equipment stock query handled by keyword matching",
            }

        # Sales/quotes must win before the retired-topics loop ("summary" etc.).
        if re.search(r"\bsales?\b|\brevenue\b|\bquotes?\b|\bsold\b|\bsage\b", normalized):
            return {
                "endpoint": "sales",
                "parameters": {},
                "reasoning": "Sales/quotes query handled by keyword matching",
            }

        # Retired data domains — their compute jobs are being rebuilt/verified.
        retired_topics = {
            "trimmers": ("trimmer",),
            "manufacturing lots": ("lot", "lots", "mfgreq", "manufacturing request"),
            "adjustments": ("adjustment",),
            "data quality": ("quality",),
            "ETA / lead time": ("eta", "lead time"),
            "manufacturing summary": ("summary", "brief", "overall status", "business status", "operations status"),
        }
        # Word-boundary match: "eta" must not fire inside "detail", etc.
        for topic, keywords in retired_topics.items():
            if any(re.search(r"\b" + re.escape(kw) + r"\b", normalized) for kw in keywords):
                return {
                    "endpoint": "retired",
                    "parameters": {"topic": topic},
                    "reasoning": f"Retired data domain ({topic}) handled by keyword matching",
                }

        if "heading" in normalized or "header" in normalized:
            # Per-head rows only when a specific head or a breakdown is requested;
            # general heading status questions get the overall plan-vs-actual rollup.
            breakdown_terms = (
                "by head", "per head", "each head", "which head", "every head",
                "by header", "per header", "each header", "which header",
                "breakdown", "break down", "all heads", "all headers", "individual",
            )
            head_match = re.search(
                r"\b(carlo[\s_-]*salvi|salvi|feng[\s_-]*pei[\s_-]*[123]?|national[\s_-]*[123]?|sp[\s_-]*11|sp[\s_-]*21[\s_-]*[12]?)\b",
                normalized,
            )
            if head_match or any(term in normalized for term in breakdown_terms):
                parameters: dict[str, Any] = {}
                if head_match:
                    parameters["head_name"] = head_match.group(1)
                return {
                    "endpoint": "heading",
                    "parameters": parameters,
                    "reasoning": "Per-head heading query handled by keyword matching",
                }
            return {
                "endpoint": "heading_overall",
                "parameters": {},
                "reasoning": "Heading status query mapped to overall plan-vs-actual",
            }

        if "inventory" in normalized or "in stock" in normalized or "on hand" in normalized or "spool" in normalized:
            parameters: dict[str, Any] = {}
            for loc in ("shed", "head", "farm"):
                if loc in normalized:
                    parameters["location"] = loc.upper()
                    break
            return {
                "endpoint": "livewire_inventory",
                "parameters": parameters,
                "reasoning": "Wire inventory query handled by keyword matching",
            }

        if any(kw in normalized for kw in ("usage", "consumption", "consumed")):
            parameters: dict[str, Any] = {}
            if "vendor" in normalized:
                parameters["dimension"] = "vendor"
            elif "dia" in normalized or "diameter" in normalized:
                parameters["dimension"] = "wire_dia"
            elif "material" in normalized:
                parameters["dimension"] = "material"
            return {
                "endpoint": "livewire_usage",
                "parameters": parameters,
                "reasoning": "Wire usage query handled by keyword matching",
            }

        if (
            "livewire" in normalized
            or "wire" in normalized
            or "demand" in normalized
            or "shortage" in normalized
            or "mild steel" in normalized
            or "aluminum" in normalized
            or "aluminium" in normalized
            or "stainless" in normalized
            or re.search(r'\b[0-9]{3,4}-[a-zA-Z]{1,3}\b', normalized)
            or ("material" in normalized and "order" in normalized)
        ):
            material_query = None
            # Explicit full code: 1010-MS, 302-SS, 5356-AL
            code_match = re.search(r'\b([0-9]{3,4}-[a-zA-Z]{1,3})\b', normalized)
            # Bare number: "302", "1010", "5356"
            number_match = re.search(r'\b([0-9]{3,4})\b', normalized)

            if code_match:
                material_query = code_match.group(1).upper()
            elif number_match:
                material_query = number_match.group(1)
            elif "mild steel" in normalized:
                material_query = "mild steel"
            elif "stainless" in normalized:
                material_query = "stainless"
            elif "aluminum" in normalized or "aluminium" in normalized:
                material_query = "aluminum"
            elif "steel" in normalized:
                material_query = "steel"

            parameters: dict[str, Any] = {}
            if material_query:
                parameters["material_query"] = material_query
            if "shortage" in normalized:
                parameters["shortages_only"] = True

            return {
                "endpoint": "livewire_demand",
                "parameters": parameters,
                "reasoning": "Livewire material ordering/demand query handled by keyword matching",
            }

        return {
            "endpoint": "help",
            "parameters": {},
            "reasoning": "No strong keyword match; returning help",
        }

    def _local_first_parse(self, query: str) -> dict[str, Any] | None:
        normalized = query.strip().lower()
        ops_keywords = {
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
            "manufacturing request",
            "lead time",
            "eta",
            "stud",
            "summary",
            "status",
            "operations",
            "livewire",
            "wire",
            "material",
            "mild steel",
            "order",
            "shortage",
            "inventory",
            "usage",
            "demand",
            "spool",
            "sales",
            "sale",
            "revenue",
            "quote",
            "quotes",
            "sold",
            "sage",
        }

        if any(keyword in normalized for keyword in self._SMALL_TALK_KEYWORDS):
            return self._fallback_parse(query)

        if (
            ("connectfastapi" in normalized or "connect fastapi" in normalized or "fastapi" in normalized)
            and any(
                token in normalized
                for token in {"connected", "connection", "online", "up", "down", "available", "health", "status", "reachable"}
            )
        ):
            return self._fallback_parse(query)

        token_count = len(normalized.split())
        if token_count <= 4 and not any(keyword in normalized for keyword in ops_keywords):
            return self._fallback_parse(query)
        return None

    def parse(self, query: str) -> dict[str, Any]:
        """Parse a user query and return the API call to make

        Returns:
            dict with keys: endpoint, parameters, reasoning
        """
        try:
            local_result = self._local_first_parse(query)
            if local_result is not None:
                enriched = self._enrich_intent(query, local_result)
                logger.info(
                    "Handled query locally before LLM: %s -> %s (confidence %.2f)",
                    query,
                    enriched.get("endpoint"),
                    enriched.get("confidence", 0.0),
                )
                return enriched

            content = self._create_completion(query)
            
            # Remove markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            result = json.loads(content)
            enriched = self._enrich_intent(query, result)
            
            logger.info(
                f"Parsed query '{query}' -> endpoint: {enriched.get('endpoint')}, "
                f"params: {enriched.get('parameters')}, confidence: {enriched.get('confidence')}"
            )
            
            return enriched

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return self._enrich_intent(query, {
                "endpoint": None,
                "parameters": {},
                "reasoning": f"Error parsing response: {e}"
            })
        except Exception as e:
            logger.error(f"Error parsing intent: {e}")
            return self._enrich_intent(query, self._fallback_parse(query))
