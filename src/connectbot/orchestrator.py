from __future__ import annotations

import json
import logging
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .answer_renderer import render
from .answer_spec import JSON_SCHEMA_PROMPT, parse_answer_spec
from .api_client import FastAPIClient
from .evidence import build_evidence_lines, build_request_preview, build_response_preview, trim_evidence
from .insights import Fact, comparison_intent, compute_facts
from .intent_parser import IntentParser
from .keyword_router import (
    _BUSINESS_KEYWORDS,
    _CAPABILITIES_HINTS,
    _EQUIPMENT_NOUN_RE,
    _FASTAPI_CONNECTIVITY_KEYWORDS,
    _FOLLOWUP_HINTS,
    _GREETING_TERMS,
    _RUNDOWN_TERMS,
    _THANKS_TERMS,
    _TOPIC_GROUPS,
    _extract_data_date,
    _has_term,
    resolve_keyword_intent,
)
from .llm_client import LLMClient
from .response_formatter import API_CATALOG, ResponseFormatter, capability_summary
from .tool_router import TOOL_DEFS, run_tool_loop

# Endpoints that expose sales/quote dollar figures — gated per user when a
# SALES_ACCESS_USERS allowlist is configured.
_SALES_ENDPOINTS = {"sales", "sage_trend"}

_SALES_RESTRICTED_MESSAGE = (
    "Sales and quote data is restricted to specific users, and your Slack account "
    "isn't on the list. Everything else — backlog, heading, and wire data — is open to you."
)

logger = logging.getLogger(__name__)

_GREETING_RESPONSES = [
    "Hi! What can I help you with today?",
    "Hey! What can I do for you?",
    "Hey there — what do you need?",
]

_CAPABILITIES_TEXT = (
    "Here's what I'm connected to right now:\n"
    + "\n".join(f"• *{name}* — {desc}" for name, desc in API_CATALOG)
    + "\n\nJust ask naturally."
)

_RETIRED_MESSAGE = (
    "That data source ({topic}) is being rebuilt and verified right now, so I can't answer it yet. "
    f"What I can pull live: {capability_summary()}."
)


@dataclass
class OrchestratorResult:
    text: str
    blocks: list
    mode: str
    endpoint: str | None = None
    model: str | None = None
    dev_info: dict | None = None


class ConnectBotOrchestrator:
    """AI-first orchestrator that separates general chat from grounded business answers."""

    def __init__(
        self,
        llm_client: LLMClient,
        fastapi_client: FastAPIClient,
        intent_parser: IntentParser,
        formatter: ResponseFormatter,
        history_window: int = 6,
        llm_provider: str = "openrouter",
        local_only_fail_closed: bool = False,
        bot_identity_name: str = "ConnectBot",
        bot_scope_name: str = "IWT / Connect operations",
        bot_voice_style: str = "concise, practical, direct, and shop-floor friendly",
        bot_personality_notes: str = "",
        social_deterministic_mode: bool = True,
        use_tool_router: bool = True,
        answer_spec_enabled: bool = True,
        sales_access_users: set[str] | None = None,
    ):
        self.llm_client = llm_client
        self.fastapi_client = fastapi_client
        self.intent_parser = intent_parser
        self.formatter = formatter
        self.history_window = history_window
        self.llm_provider = llm_provider
        self.local_only_fail_closed = local_only_fail_closed
        self.bot_identity_name = bot_identity_name
        self.bot_scope_name = bot_scope_name
        self.bot_voice_style = bot_voice_style
        self.bot_personality_notes = bot_personality_notes
        self.social_deterministic_mode = social_deterministic_mode
        self.answer_spec_enabled = answer_spec_enabled
        # Tool calling needs the OpenRouter client; Ollama's client has no
        # complete_with_tools, so the flag quietly disables itself there.
        self.use_tool_router = use_tool_router and hasattr(llm_client, "complete_with_tools")
        # Empty/None allowlist means unrestricted (everyone can see sales).
        self.sales_access_users = {u.strip() for u in (sales_access_users or set()) if u.strip()}
        self.classifier_prompt = self._build_classifier_prompt()
        self.general_prompt = self._build_general_prompt()
        self.business_prompt = self._build_business_prompt()
        self.tool_router_prompt = self._build_tool_router_prompt()
        self._history: dict[str, deque[dict[str, str]]] = defaultdict(lambda: deque(maxlen=self.history_window))
        self._business_context: dict[str, dict[str, Any]] = {}
        self._last_mode: dict[str, str] = {}

    def _build_classifier_prompt(self) -> str:
        return (
            f"You classify Slack messages for {self.bot_scope_name}. "
            "Return only JSON with keys mode and reasoning. "
            "mode must be one of: general, business. "
            "Use business for company operations, manufacturing, production, backlog, ETA, bottlenecks, lots, trimmers, repairs, wire, equipment, plating, sales, quotes, revenue, or similar internal metrics/data requests. "
            "Use business if the user asks about IWT or Connect plant operations. "
            "Use general for casual conversation, greetings, light knowledge, or questions that do not require company data."
        )

    def _build_general_prompt(self) -> str:
        notes = f" Additional personality notes: {self.bot_personality_notes}." if self.bot_personality_notes else ""
        capabilities = "; ".join(f"{name} ({desc})" for name, desc in API_CATALOG)
        return (
            f"You are {self.bot_identity_name}, a Slack assistant for {self.bot_scope_name}. "
            f"Voice style: {self.bot_voice_style}."
            " For general chat, respond naturally, clearly, and briefly. "
            "If the user message is brief (greeting/thanks), keep replies very short. "
            "Offer a relevant follow-up suggestion when it helps the user continue. "
            "You can answer general chat and best-effort general knowledge questions conversationally. "
            f"Your ONLY data capabilities are these live Connect data feeds: {capabilities}. "
            "If asked what you can do, list exactly those and nothing else — NEVER invent "
            "abilities like deploy status, incident history, runbooks, drafting documents, "
            "or service health. "
            "Do not pretend to have live external tools or real-time world data. "
            "If asked about live world facts like current weather, answer best-effort and note that you do not have a live external feed. "
            "If a question appears to be about IWT/Connect operations data, steer toward grounded FastAPI-backed answers. "
            "Reply with the final answer only — never show reasoning or working notes. "
            "Format for Slack mrkdwn (*single asterisks* for bold, no markdown tables or headings)."
            f"{notes}"
        )

    def _build_business_prompt(self, spec: bool | None = None) -> str:
        """Grounding rules plus output rules: the JSON answer spec when
        structured answers are on, otherwise Slack-mrkdwn prose."""
        if spec is None:
            spec = self.answer_spec_enabled
        notes = f" Additional personality notes: {self.bot_personality_notes}." if self.bot_personality_notes else ""
        grounding = (
            f"You are {self.bot_identity_name}, an internal assistant for {self.bot_scope_name}. "
            f"Voice style: {self.bot_voice_style}. "
            "Answer the user's business question using the supplied FastAPI evidence as the source of truth. "
            "Do not invent values or claim certainty beyond the data. "
            "If the evidence is limited, say so briefly. "
            "Earlier conversation turns may be included for context — use them to resolve "
            "follow-up references (like 'and yesterday?'), but answer strictly from the "
            "FastAPI evidence in the latest message. "
            "You may add short, clearly-labeled best-practice guidance from general knowledge only when it does not conflict with the evidence. "
        )
        if spec:
            rules = (
                "\n\nOutput rules — follow strictly:\n"
                "- Never show your reasoning, working notes, or planning. No preamble.\n"
                "- The headline answers the question like a knowledgeable coworker in chat, leading "
                "with the headline number — e.g. \"We're about 70% to today's goal at 257,800 studs\".\n"
                "- Pick the numbers that answer the question — not every field. For many rows, "
                "show a table and call out the best, worst, and any outliers in the headline or body.\n"
                "- Round numbers in text sensibly (whole percents, thousands with commas, dollars like "
                "$39,800) and use human labels, not raw field names like vs_plan_pct.\n\n"
                f"{JSON_SCHEMA_PROMPT}"
            )
        else:
            rules = (
                "\n\nOutput rules — follow strictly:\n"
                "- Reply with the final answer ONLY. Never show your reasoning, working notes, "
                "planning, or phrases like 'We need to answer' or 'Let's extract'. No preamble.\n"
                "- Format for Slack mrkdwn: *single asterisks* for bold, plain `•` or `-` bullets. "
                "No markdown tables, no # headings, no **double asterisks**.\n"
                "- Tone: answer like a knowledgeable coworker in chat. For status questions "
                "('how's X today?'), reply with 1-2 short conversational sentences that lead with "
                "the headline numbers — e.g. \"We're about 70% to today's goal at 257,800 studs, "
                'running 12% of planned rate." No bullets for these.\n'
                "- Keep it tight: headline numbers only, not every stat in the evidence. The user "
                "sees the conversation history and will ask a follow-up if they want more detail.\n"
                "- Use short bullet lists ONLY when the user explicitly asks for a breakdown, list, "
                "or comparison across many items — and even then keep it under about 12 lines.\n"
                "- Pick the numbers that answer the question — do not dump every row or every field. "
                "When there are many rows, summarize the group and call out the best, worst, and any outliers.\n"
                "- Round numbers sensibly (whole percents, thousands with commas, dollars like $39,800) "
                "and use human labels, not raw field names like vs_plan_pct.\n"
                "- When the evidence includes a trend percent vs a previous day, phrase it naturally "
                "(e.g. 'up 12% from yesterday' or 'down 13% from yesterday')."
            )
        return grounding + rules + notes

    def _build_tool_router_prompt(self) -> str:
        return (
            self.business_prompt + "\n\nTool use:\n"
            "- Call the tools needed to answer; combine several when the question spans "
            "topics (a daily rundown = heading_overall + sales + backlog).\n"
            "- OMIT data_date to get the latest data — never guess a date. Only pass "
            "data_date when the user names a specific day.\n"
            "- Never invent order quantities — wire-ordering numbers must come verbatim "
            "from livewire_demand rows.\n"
            "- Don't repeat a tool call with identical arguments. Once you have the data, "
            "give the final answer."
        )

    def _user_can_see_sales(self, user_id: str) -> bool:
        if not self.sales_access_users:
            return True
        return user_id in self.sales_access_users

    def process_query(
        self,
        conversation_id: str,
        user_query: str,
        is_dev: bool = False,
        user_id: str = "",
    ) -> OrchestratorResult:
        mode, classifier_model = self._classify_query(conversation_id, user_query)
        logger.info("Orchestrator classified query as %s", mode)
        if mode == "business":
            result = self._handle_business_query(conversation_id, user_query, is_dev=is_dev, user_id=user_id)
        else:
            result = self._handle_general_query(conversation_id, user_query)

        if classifier_model and result.model is None:
            result.model = classifier_model
        self._last_mode[conversation_id] = mode
        self._remember(conversation_id, "user", user_query)
        self._remember(conversation_id, "assistant", result.text)

        if is_dev:
            dev = {
                "classified_mode": mode,
                "result_mode": result.mode,
                "endpoint": result.endpoint,
                "model": result.model,
                "conversation_id": conversation_id,
            }
            if result.dev_info:
                dev.update(result.dev_info)
            result.dev_info = dev

        return result

    def _is_short_followup(self, user_query: str) -> bool:
        normalized = re.sub(r"\s+", " ", user_query.strip().lower())
        if not normalized:
            return False
        if len(normalized.split()) > 7:
            return False
        return any(normalized.startswith(hint) or hint in normalized for hint in _FOLLOWUP_HINTS)

    def _remember(self, conversation_id: str, role: str, content: str) -> None:
        self._history[conversation_id].append({"role": role, "content": content})

    def _classify_query(self, conversation_id: str, user_query: str) -> tuple[str, str | None]:
        normalized = user_query.lower()
        if self._is_fastapi_connectivity_query(user_query):
            return "general", None

        if self.social_deterministic_mode and self._is_social_deterministic_query(user_query):
            return "general", None

        if any(keyword in normalized for keyword in _BUSINESS_KEYWORDS):
            return "business", None

        if self._is_short_followup(user_query):
            last_mode = self._last_mode.get(conversation_id)
            if last_mode in {"general", "business"}:
                return last_mode, None

        history = list(self._history.get(conversation_id, []))[-4:]
        messages = [{"role": "system", "content": self.classifier_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_query})

        try:
            content, model = self.llm_client.complete(messages)
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            payload = json.loads(content)
            mode = payload.get("mode", "general")
            return ("business" if mode == "business" else "general"), model
        except Exception as error:
            logger.warning("Falling back to general classification: %s", error)
            return "general", None

    def _is_social_deterministic_query(self, user_query: str) -> bool:
        """Fast check to avoid LLM classification for common social prompts."""
        normalized = re.sub(r"\s+", " ", user_query.strip().lower())
        if not normalized:
            return False

        if _has_term(normalized, _GREETING_TERMS):
            return True
        if _has_term(normalized, _THANKS_TERMS):
            return True

        if normalized in {
            "who are you",
            "what are you",
            "what is your name",
            "what's your name",
            "help",
            "capabilities",
            "how are you",
            "how's it going",
            "hows it going",
        }:
            return True

        return any(hint in normalized for hint in _CAPABILITIES_HINTS)

    def _handle_general_query(self, conversation_id: str, user_query: str) -> OrchestratorResult:
        if self._is_fastapi_connectivity_query(user_query):
            return self._build_fastapi_connectivity_result()

        if self.social_deterministic_mode:
            deterministic = self._build_social_deterministic_result(user_query)
            if deterministic is not None:
                return deterministic

        history = list(self._history.get(conversation_id, []))[-4:]
        messages = [{"role": "system", "content": self.general_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_query})

        try:
            answer, model = self.llm_client.complete(messages)
            return OrchestratorResult(
                text=answer,
                blocks=self.formatter.format_chat(answer),
                mode="general",
                model=model,
            )
        except Exception as error:
            logger.error("General chat failed, falling back locally: %s", error)
            if self.local_only_fail_closed and self.llm_provider == "ollama":
                text = "Local AI is temporarily unavailable. Please try again in a moment."
                return OrchestratorResult(
                    text=text,
                    blocks=self.formatter.format_chat(text),
                    mode="general-local-unavailable",
                )
            fallback = self.intent_parser._fallback_parse(user_query)
            if fallback.get("endpoint") == "chat":
                message = fallback.get("parameters", {}).get("message", "I’m here.")
                return OrchestratorResult(
                    text=message,
                    blocks=self.formatter.format_chat(message),
                    mode="general-fallback",
                )
            return OrchestratorResult(
                text=self.formatter.to_text("help"),
                blocks=self.formatter.format_help(),
                mode="general-fallback",
            )

    def _build_social_deterministic_result(self, user_query: str) -> OrchestratorResult | None:
        normalized = re.sub(r"\s+", " ", user_query.strip().lower())
        if not normalized:
            return None

        pick = abs(hash(normalized)) % 3

        # Capabilities beats greeting so "hi, what can you do?" gets the list,
        # not just "Hey!".
        if normalized in {"help", "capabilities"} or any(hint in normalized for hint in _CAPABILITIES_HINTS):
            text = _CAPABILITIES_TEXT
            return OrchestratorResult(text=text, blocks=self.formatter.format_chat(text), mode="general-social")

        if _has_term(normalized, _GREETING_TERMS):
            text = _GREETING_RESPONSES[pick]
            return OrchestratorResult(text=text, blocks=self.formatter.format_chat(text), mode="general-social")

        if _has_term(normalized, _THANKS_TERMS):
            text = "Anytime!"
            return OrchestratorResult(text=text, blocks=self.formatter.format_chat(text), mode="general-social")

        if normalized in {"who are you", "what are you", "what is your name", "what's your name"}:
            text = (
                f"I'm {self.bot_identity_name} — I answer questions with live Connect data. "
                "Ask me 'what can you do?' for the full list."
            )
            return OrchestratorResult(text=text, blocks=self.formatter.format_chat(text), mode="general-social")

        if normalized in {"how are you", "how's it going", "hows it going"}:
            text = "Running well! What do you need?"
            return OrchestratorResult(text=text, blocks=self.formatter.format_chat(text), mode="general-social")

        return None

    def _is_fastapi_connectivity_query(self, user_query: str) -> bool:
        normalized = user_query.strip().lower()
        if not normalized:
            return False

        connectivity_signals = {
            "connected",
            "connection",
            "online",
            "reachable",
            "reach",
            "up",
            "down",
            "available",
            "health",
            "alive",
            "status",
        }

        has_fastapi_term = any(term in normalized for term in _FASTAPI_CONNECTIVITY_KEYWORDS)
        has_connectivity_signal = any(term in normalized for term in connectivity_signals)
        return has_fastapi_term and has_connectivity_signal

    def _build_fastapi_connectivity_result(self) -> OrchestratorResult:
        health = self.fastapi_client.get_health()

        if health is None:
            text = "I cannot reach ConnectFastAPI right now."
            return OrchestratorResult(
                text=text,
                blocks=self.formatter.format_chat(text),
                mode="general-fastapi-check",
            )

        db_connected = bool(health.get("db_connected", False))
        jobs = health.get("jobs") if isinstance(health.get("jobs"), list) else []

        if db_connected:
            text = "Yes. I am connected to ConnectFastAPI."
            if jobs:
                failing_jobs = [
                    str(job.get("job_name", "unknown"))
                    for job in jobs
                    if str(job.get("status", "")).strip().lower() not in {"success", "ok"}
                ]
                if failing_jobs:
                    preview = ", ".join(failing_jobs[:3])
                    text += f" Recent compute job issues detected: {preview}."
        else:
            text = "I can reach ConnectFastAPI, but it reports the database as disconnected."

        return OrchestratorResult(
            text=text,
            blocks=self.formatter.format_chat(text),
            mode="general-fastapi-check",
        )

    def try_social_response(self, user_query: str) -> OrchestratorResult | None:
        """Return an instant deterministic result for social queries, or None if an LLM/API call is needed."""
        if not self.social_deterministic_mode:
            return None
        if self._is_fastapi_connectivity_query(user_query):
            return None
        return self._build_social_deterministic_result(user_query)

    def _resolve_keyword_intent(self, user_query: str) -> dict[str, Any] | None:
        return resolve_keyword_intent(user_query)

    def _resolve_followup_intent(self, conversation_id: str, user_query: str) -> dict[str, Any] | None:
        """Reuse the previous business endpoint for short follow-ups ("and yesterday?")."""
        normalized = re.sub(r"\s+", " ", user_query.strip().lower())
        data_date = _extract_data_date(normalized)
        is_bare_date = data_date is not None and len(normalized.split()) <= 3
        if not (self._is_short_followup(user_query) or is_bare_date):
            return None
        context = self._business_context.get(conversation_id)
        if not context or not context.get("endpoint"):
            return None
        params = dict(context.get("parameters") or {})
        if data_date:
            params["data_date"] = data_date
        return {
            "endpoint": context["endpoint"],
            "parameters": params,
            "reasoning": "short follow-up reusing previous business endpoint",
            "confidence": 0.75,
        }

    def _handle_tool_router_query(
        self, conversation_id: str, user_query: str, is_dev: bool = False, user_id: str = ""
    ) -> OrchestratorResult | None:
        """Let the LLM pick (possibly several) endpoints via tool calling.

        Returns None on any failure so the caller falls back to legacy routing.
        """
        history = [message for message in list(self._history.get(conversation_id, []))[-4:] if message.get("content")]
        # Computed per call — the bot process runs for days, and the model
        # will otherwise guess dates from its training data.
        system_prompt = f"{self.tool_router_prompt}\nToday's date is {date.today().isoformat()}."
        tools = TOOL_DEFS
        if not self._user_can_see_sales(user_id):
            tools = [t for t in TOOL_DEFS if t["function"]["name"] not in _SALES_ENDPOINTS]
            system_prompt += (
                "\nSales/quote data is not available to this user — answer from the "
                "other data sources and say sales figures are restricted if asked for them."
            )
        evidence: dict[str, Any] = {}
        facts: list[Fact] = []

        def call_tool(intent: dict[str, Any]) -> dict[str, Any] | None:
            data = self._call_api(intent)
            if data is None:
                return None
            tool_facts = self._gather_facts(str(intent.get("endpoint")), intent.get("parameters") or {}, data)
            if not tool_facts:
                return data
            facts.extend(tool_facts)
            return {**data, "computed_facts": [{"id": f.id, "text": f.text} for f in tool_facts]}

        try:
            answer, model, tools_used = run_tool_loop(
                self.llm_client,
                call_tool,
                self._trim_evidence,
                system_prompt,
                history,
                user_query,
                tools=tools,
                evidence=evidence,
            )
        except Exception as error:
            logger.warning("Tool router failed, falling back to legacy routing: %s", error)
            return None

        endpoints = ", ".join(dict.fromkeys(item["tool"] for item in tools_used)) or None
        text, blocks, spec_dev = self._render_answer(answer, evidence, facts=facts)
        dev_info = None
        if is_dev:
            dev_info = {"decision_path": ["tool_router"], "tools_used": tools_used, **spec_dev}
        return OrchestratorResult(
            text=text,
            blocks=blocks,
            mode="business-tool-router",
            endpoint=endpoints,
            model=model,
            dev_info=dev_info,
        )

    def _handle_business_query(
        self, conversation_id: str, user_query: str, is_dev: bool = False, user_id: str = ""
    ) -> OrchestratorResult:
        normalized_query = re.sub(r"\s+", " ", user_query.strip().lower())
        wants_rundown = any(term in normalized_query for term in _RUNDOWN_TERMS)
        multi_topic = (
            sum(1 for keywords in _TOPIC_GROUPS.values() if any(kw in normalized_query for kw in keywords)) >= 2
        )
        # "How many machines have we sold?" trips both the sales group (sold)
        # and the equipment group (machine) but is ONE topic — equipment units.
        # Collapse the double count only when the sales hit is just sold/sales
        # wording, so genuine "machine revenue and quotes" still multi-routes.
        if multi_topic and _EQUIPMENT_NOUN_RE.search(normalized_query):
            sales_hits = {kw for kw in _TOPIC_GROUPS["sales"] if kw in normalized_query}
            if sales_hits and sales_hits <= {"sold", "sales"}:
                multi_topic = (
                    sum(
                        1
                        for topic, keywords in _TOPIC_GROUPS.items()
                        if topic != "sales" and any(kw in normalized_query for kw in keywords)
                    )
                    >= 2
                )

        intent = None
        # Rundown/multi-topic questions need several endpoints, which only the
        # tool router can compose — skip the single-endpoint keyword rules.
        if not (self.use_tool_router and (wants_rundown or multi_topic)):
            intent = self._resolve_keyword_intent(user_query) or self._resolve_followup_intent(
                conversation_id, user_query
            )
        if intent is None and self.use_tool_router:
            tool_result = self._handle_tool_router_query(conversation_id, user_query, is_dev=is_dev, user_id=user_id)
            if tool_result is not None:
                return tool_result
        if intent is None:
            intent = self.intent_parser.parse(user_query)
        endpoint = intent.get("endpoint")

        # Per-user gate on sales/quote dollar figures, whatever path resolved
        # the intent (keyword, follow-up, or legacy parser).
        if endpoint in _SALES_ENDPOINTS and not self._user_can_see_sales(user_id):
            return OrchestratorResult(
                text=_SALES_RESTRICTED_MESSAGE,
                blocks=self.formatter.format_chat(_SALES_RESTRICTED_MESSAGE),
                mode="business-restricted",
                endpoint=endpoint,
            )
        decision_path = [
            f"parse_intent(endpoint={endpoint})",
            f"intent_confidence({intent.get('confidence', 'n/a')})",
        ]
        request_preview = build_request_preview(self.fastapi_client.base_url, endpoint, intent.get("parameters", {}))
        base_dev_info = (
            {
                "intent_reasoning": intent.get("reasoning"),
                "intent_confidence": intent.get("confidence"),
                "intent_parameters": intent.get("parameters", {}),
                "fastapi_request": request_preview,
                "decision_path": decision_path,
            }
            if is_dev
            else None
        )

        if endpoint == "retired":
            if base_dev_info is not None:
                base_dev_info["decision_path"].append("retired_data_domain")
            topic = intent.get("parameters", {}).get("topic", "that data")
            text = _RETIRED_MESSAGE.format(topic=topic)
            return OrchestratorResult(
                text=text,
                blocks=self.formatter.format_chat(text),
                mode="business-retired",
                endpoint=endpoint,
                dev_info=base_dev_info,
            )

        confidence = float(intent.get("confidence", 0.0))
        if confidence < 0.4 and endpoint not in {"help", "chat", None}:
            if base_dev_info is not None:
                base_dev_info["decision_path"].append("clarify_low_confidence_intent")
            prompt = f"I might have mapped that wrong. Do you want {capability_summary()}?"
            return OrchestratorResult(
                text=prompt,
                blocks=self.formatter.format_chat(prompt),
                mode="business-clarification",
                dev_info=base_dev_info,
            )

        if endpoint in {"help", "chat", None}:
            if base_dev_info is not None:
                base_dev_info["decision_path"].append("unsupported_business_endpoint")
            text = (
                "I understood that as a business-style request, but I couldn't map it to a supported data call yet. "
                f"Try asking about {capability_summary()}."
            )
            return OrchestratorResult(
                text=text,
                blocks=self.formatter.format_unsupported_query(),
                mode="business-unsupported",
                dev_info=base_dev_info,
            )

        response_data = self._call_api(intent)
        if response_data is None:
            if base_dev_info is not None:
                base_dev_info["decision_path"].append("fastapi_unavailable")
            text = f"I understood your request, but live data for {endpoint} is unavailable right now."
            return OrchestratorResult(
                text=text,
                blocks=self.formatter.format_api_unavailable(endpoint),
                mode="business-unavailable",
                endpoint=endpoint,
                dev_info=(
                    {
                        **base_dev_info,
                        "fastapi_response": {"status": "unavailable"},
                        "fastapi_response_raw": None,
                    }
                    if base_dev_info is not None
                    else None
                ),
            )

        if base_dev_info is not None:
            base_dev_info["decision_path"].append("fastapi_response_received")

        empty_result = self._build_empty_data_result(
            endpoint,
            response_data,
            intent,
            dev_info=(
                {
                    **base_dev_info,
                    "fastapi_response": build_response_preview(response_data),
                    "fastapi_response_raw": response_data,
                }
                if base_dev_info is not None
                else None
            ),
        )
        if empty_result is not None:
            if empty_result.dev_info is not None:
                empty_result.dev_info.setdefault("decision_path", []).append("return_deterministic_empty_result")
            return empty_result

        if endpoint == "livewire_demand":
            text = self.formatter.to_text(endpoint, response_data)
            blocks = self.formatter.format_livewire_demand(response_data)
            self._remember_business_context(conversation_id, endpoint, intent, response_data)
            dev_info = None
            if is_dev:
                dev_info = {
                    **(base_dev_info or {}),
                    "fastapi_response": build_response_preview(response_data),
                    "fastapi_response_raw": response_data,
                    "evidence_lines": build_evidence_lines(endpoint, response_data),
                }
                dev_info.setdefault("decision_path", []).append("return_deterministic_livewire_format")
            return OrchestratorResult(
                text=text,
                blocks=blocks,
                mode="business-grounded",
                endpoint=endpoint,
                model=None,
                dev_info=dev_info,
            )

        evidence_data = self._trim_evidence(endpoint, response_data)
        evidence_text = json.dumps(evidence_data, default=str, indent=2)
        facts = self._gather_facts(endpoint, intent.get("parameters") or {}, response_data)
        facts_text = ""
        if facts:
            facts_json = json.dumps([{"id": f.id, "text": f.text} for f in facts])
            facts_text = f"\n\nComputed facts (cite by id in insights):\n{facts_json}"
        history = [message for message in list(self._history.get(conversation_id, []))[-4:] if message.get("content")]
        messages = [
            {"role": "system", "content": self.business_prompt},
            *history,
            {
                "role": "user",
                "content": (
                    f"User question: {user_query}\n\n"
                    f"Mapped endpoint: {endpoint}\n"
                    f"Parameters: {json.dumps(intent.get('parameters', {}), default=str)}\n\n"
                    f"FastAPI evidence:\n{evidence_text}"
                    f"{facts_text}"
                ),
            },
        ]

        try:
            if base_dev_info is not None:
                base_dev_info["decision_path"].append("invoke_llm_business_synthesis")
            answer, model = self.llm_client.complete(messages)
        except Exception as error:
            if self.local_only_fail_closed and self.llm_provider == "ollama":
                logger.error("Local business synthesis failed in fail-closed mode: %s", error)
                text = "I couldn't reach the local AI engine right now. Please try again shortly."
                return OrchestratorResult(
                    text=text,
                    blocks=self.formatter.format_chat(text),
                    mode="business-local-unavailable",
                    endpoint=endpoint,
                    dev_info=(
                        {
                            **base_dev_info,
                            "fastapi_response": build_response_preview(response_data),
                            "fastapi_response_raw": response_data,
                            "llm_error": str(error),
                        }
                        if base_dev_info is not None
                        else None
                    ),
                )
            logger.error("Business synthesis failed, using structured fallback: %s", error)
            answer = self.formatter.to_text(endpoint, response_data)
            model = None
            if base_dev_info is not None:
                base_dev_info["decision_path"].append("llm_failed_use_structured_fallback")

        text, blocks, spec_dev = self._render_answer(
            answer, {endpoint: response_data}, llm_ok=model is not None, facts=facts
        )
        evidence_lines = build_evidence_lines(endpoint, response_data)
        self._remember_business_context(conversation_id, endpoint, intent, response_data)
        dev_info = None
        if is_dev:
            dev_info = {
                **(base_dev_info or {}),
                **spec_dev,
                "fastapi_response": build_response_preview(response_data),
                "fastapi_response_raw": response_data,
                "evidence_lines": evidence_lines,
            }
            dev_info.setdefault("decision_path", []).append("return_business_grounded_result")
        return OrchestratorResult(
            text=text,
            blocks=blocks,
            mode="business-grounded",
            endpoint=endpoint,
            model=model,
            dev_info=dev_info,
        )

    def _render_answer(
        self,
        answer: str,
        evidence: dict[str, Any],
        llm_ok: bool = True,
        facts: list[Fact] | None = None,
    ) -> tuple[str, list, dict[str, Any]]:
        """Render the LLM reply: an answer spec when it parses, else prose.

        Returns (text, blocks, dev fields). The prose fallback is the
        pre-spec output, so a model that ignores the JSON instruction still answers.
        """
        if not self.answer_spec_enabled:
            return answer, self.formatter.format_grounded_answer(answer), {}
        facts = facts or []
        spec = parse_answer_spec(answer, evidence, {f.id for f in facts}) if llm_ok else None
        if spec is None:
            reason = "reply_not_a_spec" if llm_ok else "llm_failed"
            logger.info("Answer spec fallback: %s", reason)
            dev = {"answer_layout": "prose", "spec_fallback_reason": reason}
            return answer, self.formatter.format_grounded_answer(answer), dev
        text, blocks = render(spec, evidence, {f.id: f.severity for f in facts})
        logger.info("Answer spec rendered: layout=%s", spec.layout)
        return (
            text,
            blocks,
            {"answer_layout": spec.layout, "answer_spec": asdict(spec), "facts": [asdict(f) for f in facts]},
        )

    def _gather_facts(self, endpoint: str, params: dict[str, Any], data: dict[str, Any]) -> list[Fact]:
        """Computed facts for one endpoint's data, fetching its comparison
        (prior workday, trailing average, backlog total) when it has one.
        A failed comparison just means fewer facts; it never fails the answer."""
        if not self.answer_spec_enabled:
            return []
        try:
            comparison_request = comparison_intent(endpoint, params, data)
            comparison = self._call_api(comparison_request) if comparison_request else None
            return compute_facts(endpoint, data, comparison)
        except Exception as error:
            logger.warning("Computing facts for %s failed: %s", endpoint, error)
            return []

    @staticmethod
    def _trim_evidence(endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        return trim_evidence(endpoint, data)

    def _remember_business_context(
        self,
        conversation_id: str,
        endpoint: str,
        intent: dict[str, Any],
        response_data: dict[str, Any],
    ) -> None:
        self._business_context[conversation_id] = {
            "endpoint": endpoint,
            "parameters": dict(intent.get("parameters") or {}),
        }

    def _build_empty_data_result(
        self,
        endpoint: str,
        data: dict[str, Any],
        intent: dict[str, Any],
        dev_info: dict | None = None,
    ) -> OrchestratorResult | None:
        """Return a deterministic result when the API response has no actionable rows.

        Prevents the LLM from hallucinating answers (e.g. inventing order quantities)
        when evidence is empty. Returns None if the data is non-empty and should proceed
        to LLM synthesis.
        """
        if endpoint == "livewire_demand":
            rows = data.get("rows", [])
            if not rows:
                parameters = intent.get("parameters", {})
                material_query = parameters.get("material_query")
                empty_reason = "filtered_no_rows" if material_query else "snapshot_empty"
                if material_query:
                    text = (
                        f"No livewire demand rows matched '{material_query}' in the latest compute snapshot. "
                        "This may mean naming mismatch (material code/name) or no demand rows for that filter."
                    )
                else:
                    text = (
                        "Livewire demand snapshot is currently empty, so I cannot confirm order quantities yet. "
                        "Please run/verify connectCompute's livewire_material_shortage job and then retry."
                    )
                return OrchestratorResult(
                    text=text,
                    blocks=self.formatter.format_grounded_answer(text),
                    mode="business-grounded",
                    endpoint=endpoint,
                    dev_info=(
                        {
                            **dev_info,
                            "empty_result_reason": empty_reason,
                            "empty_result_filter": material_query,
                        }
                        if dev_info is not None
                        else None
                    ),
                )
        return None

    def _call_api(self, intent: dict[str, Any]) -> dict[str, Any] | None:
        endpoint = intent.get("endpoint")
        params = intent.get("parameters", {})

        try:
            if endpoint == "backlog":
                return self.fastapi_client.get_backlog()
            if endpoint == "backlog_breakdown":
                # params doubles as the filter dict — the client only reads
                # the known filter keys out of it.
                return self.fastapi_client.get_backlog_breakdown(
                    group_by=params.get("group_by") or "stud_size",
                    limit=int(params.get("limit") or 10),
                    filters=params,
                )
            if endpoint == "backlog_lots":
                return self.fastapi_client.get_backlog_lots(
                    limit=int(params.get("limit") or 100),
                    filters=params,
                )
            if endpoint == "heading_summary":
                return self.fastapi_client.get_heading_summary(
                    data_date=params.get("data_date"),
                )
            if endpoint == "sage_trend":
                return self.fastapi_client.get_sage_trend(
                    days=int(params.get("days") or 30),
                    end_date=params.get("end_date"),
                )
            if endpoint == "compute_runs":
                return self.fastapi_client.get_compute_runs(
                    job_name=params.get("job_name"),
                    limit=int(params.get("limit") or 50),
                )
            if endpoint == "heading":
                return self.fastapi_client.get_heading(
                    head_name=params.get("head_name"),
                    data_date=params.get("data_date"),
                )
            if endpoint == "heading_overall":
                return self.fastapi_client.get_heading_overall(
                    data_date=params.get("data_date"),
                )
            if endpoint == "livewire_demand":
                return self.fastapi_client.get_livewire_demand(
                    shortages_only=bool(params.get("shortages_only", False)),
                    material_query=params.get("material_query"),
                )
            if endpoint == "livewire_inventory":
                return self.fastapi_client.get_livewire_inventory(
                    location=params.get("location"),
                )
            if endpoint == "livewire_usage":
                return self.fastapi_client.get_livewire_usage(
                    dimension=params.get("dimension"),
                )
            if endpoint == "equipment_sold":
                data = self.fastapi_client.get_equipment_sold(
                    window=params.get("window") or "week",
                    start_date=params.get("start_date"),
                    end_date=params.get("end_date"),
                    equip_type=params.get("equip_type"),
                    group_by=params.get("group_by") or "model",
                )
                if data is None:
                    return None
                return {
                    **data,
                    "field_notes": (
                        "All numbers are UNITS (machine/gun counts), never dollars. "
                        "'Sold' = order booked in Sage. machine_units is the machine "
                        "count; rows are the per-model breakdown."
                    ),
                }
            if endpoint == "equipment_stock":
                return self.fastapi_client.get_equipment_stock(
                    equip_type=params.get("equip_type"),
                    model=params.get("model"),
                )
            if endpoint == "equipment_builds":
                return self.fastapi_client.get_equipment_builds(
                    status=params.get("status"),
                    stage=params.get("stage"),
                )
            if endpoint == "equipment_parts":
                return self.fastapi_client.get_equipment_parts(
                    low_only=bool(params.get("low_only", True)),
                    limit=int(params.get("limit") or 50),
                )
            if endpoint == "equipment_repairs":
                return self.fastapi_client.get_equipment_repairs(
                    stage=params.get("stage"),
                )
            if endpoint == "equipment_repairs_history":
                return self.fastapi_client.get_equipment_repair_history(
                    customer=params.get("customer"),
                    start_date=params.get("start_date"),
                    end_date=params.get("end_date"),
                    limit=int(params.get("limit") or 100),
                )
            if endpoint == "sales":
                daily = self.fastapi_client.get_sage_daily(data_date=params.get("data_date"))
                summary = self.fastapi_client.get_sage_summary(data_date=params.get("data_date"))
                if daily is None and summary is None:
                    return None
                return {
                    "daily": daily,
                    "summary": summary,
                    "field_notes": (
                        "All values are dollars unless named *_orders. "
                        "quotes_trend_pct / sales_trend_pct are percent CHANGE vs the "
                        "previous business day (negative = lower than yesterday). "
                        "While data_date is today the day is still in progress, so the "
                        "trend pct compares a partial day with a full one — don't quote it "
                        "for today; compare against the computed facts' averages instead."
                    ),
                }
            return None
        except Exception as error:
            logger.error("Error calling API endpoint %s: %s", endpoint, error)
            return None
