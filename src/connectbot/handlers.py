"""
Slack event handlers for the Connect Bot
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any, Callable

from slack_bolt import App

from .api_client import FastAPIClient
from .config import BotSettings
from .intent_parser import IntentParser
from .ollama_client import OllamaClient
from .openrouter_client import OpenRouterClient
from .orchestrator import ConnectBotOrchestrator
from .response_formatter import ResponseFormatter

if TYPE_CHECKING:
    from slack_bolt.response import BoltResponse

logger = logging.getLogger(__name__)

_LOADING_FRAMES = (".", "..", "...")


def _handle_local_only_response(
    formatter: ResponseFormatter,
    intent: dict[str, Any],
) -> tuple[str, list]:
    endpoint = intent.get("endpoint")
    if endpoint == "chat":
        message = intent.get("parameters", {}).get("message", "I’m here.")
        return formatter.to_text("chat", {"message": message}), formatter.format_chat(message)
    if endpoint == "help":
        return formatter.to_text("help"), formatter.format_help()
    return "I can't help with that yet.", formatter.format_unsupported_query()


def _handle_fastapi_healthcheck_response(
    fastapi_client: FastAPIClient,
    formatter: ResponseFormatter,
) -> tuple[str, list]:
    health = fastapi_client.get_health()
    if health is None:
        message = "I cannot reach ConnectFastAPI right now."
        return message, formatter.format_chat(message)

    db_connected = bool(health.get("db_connected", False))
    jobs = health.get("jobs") if isinstance(health.get("jobs"), list) else []

    if db_connected:
        message = "Yes. I am connected to ConnectFastAPI."
        if jobs:
            failing_jobs = [
                str(job.get("job_name", "unknown"))
                for job in jobs
                if str(job.get("status", "")).strip().lower() not in {"success", "ok"}
            ]
            if failing_jobs:
                preview = ", ".join(failing_jobs[:3])
                message += f" Recent compute job issues detected: {preview}."
    else:
        message = "I can reach ConnectFastAPI, but it reports the database as disconnected."
    return message, formatter.format_chat(message)


def _build_intent_clarification(intent: dict[str, Any]) -> tuple[str, list] | None:
    endpoint = intent.get("endpoint")
    confidence = float(intent.get("confidence", 0.0))

    if confidence < 0.4 and endpoint not in {"help", "chat", None}:
        text = "I might have mapped that wrong. Ask for backlog, heading metrics, wire inventory, wire usage, or wire demand."
        return text, ResponseFormatter.format_chat(text)

    return None


_DEV_PREFIX = "[DEV]"
_DEV_FULL_PREFIX = "[DEV FULL]"


def _get_dev_mode(text: str) -> str | None:
    normalized = text.strip().upper()
    if normalized.startswith(_DEV_FULL_PREFIX):
        return "dev_full"
    if normalized.startswith(_DEV_PREFIX):
        return "dev"
    return None


def _strip_dev_prefix(text: str) -> str:
    stripped = text.strip()
    if stripped.upper().startswith(_DEV_FULL_PREFIX):
        return stripped[len(_DEV_FULL_PREFIX):].strip()
    if stripped.upper().startswith(_DEV_PREFIX):
        return stripped[len(_DEV_PREFIX):].strip()
    return stripped


def _post_dev_full_trace_message(
    client: Any,
    channel: str,
    thread_ts: str | None,
    dev_info: dict | None,
) -> None:
    if not dev_info:
        return

    payload = {
        "mode": dev_info.get("classified_mode"),
        "result_mode": dev_info.get("result_mode"),
        "endpoint": dev_info.get("endpoint"),
        "model": dev_info.get("model"),
        "decision_path": dev_info.get("decision_path"),
        "intent_reasoning": dev_info.get("intent_reasoning"),
        "intent_confidence": dev_info.get("intent_confidence"),
        "intent_parameters": dev_info.get("intent_parameters"),
        "empty_result_reason": dev_info.get("empty_result_reason"),
        "empty_result_filter": dev_info.get("empty_result_filter"),
        "fastapi_request": dev_info.get("fastapi_request"),
        "fastapi_response_preview": dev_info.get("fastapi_response"),
        "fastapi_response_raw": dev_info.get("fastapi_response_raw"),
        "evidence_lines": dev_info.get("evidence_lines"),
        "llm_error": dev_info.get("llm_error"),
    }
    raw = json.dumps(payload, indent=2, default=str)
    max_len = 3500
    if len(raw) > max_len:
        raw = raw[:max_len] + "\n... (truncated)"

    try:
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text="DEV FULL trace",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*DEV FULL Trace*\n```" + raw + "```",
                    },
                }
            ],
        )
    except Exception as error:
        logger.warning("Unable to post DEV FULL trace: %s", error)


def _append_dev_blocks(blocks: list, dev_info: dict | None) -> list:
    """Append a dev/trace context block to an existing blocks list."""
    if not dev_info:
        return blocks
    lines = []
    if dev_info.get("classified_mode"):
        lines.append(f"*Mode:* {dev_info['classified_mode']} → {dev_info.get('result_mode', '?')}")
    if dev_info.get("endpoint"):
        lines.append(f"*Endpoint:* {dev_info['endpoint']}")
    if dev_info.get("intent_parameters"):
        lines.append(f"*Params:* {dev_info['intent_parameters']}")
    if dev_info.get("intent_confidence") is not None:
        lines.append(f"*Confidence:* {dev_info['intent_confidence']:.2f}")
    if dev_info.get("intent_reasoning"):
        lines.append(f"*Reasoning:* {dev_info['intent_reasoning']}")
    if dev_info.get("model"):
        lines.append(f"*Model:* {dev_info['model']}")
    if dev_info.get("decision_path"):
        lines.append("*Decision Path:* " + " -> ".join(dev_info["decision_path"]))
    if dev_info.get("empty_result_reason"):
        lines.append(f"*Empty Result Reason:* {dev_info['empty_result_reason']}")
    request = dev_info.get("fastapi_request")
    if request:
        lines.append(
            "*FastAPI Request:* "
            f"{request.get('method', 'GET')} {request.get('path', 'unknown')} "
            f"params={json.dumps(request.get('params', {}), default=str)}"
        )
        if request.get("url"):
            lines.append(f"*FastAPI URL:* {request.get('url')}")
    response = dev_info.get("fastapi_response")
    if response:
        lines.append(
            "*FastAPI Response Preview:* " + json.dumps(response, default=str)
        )
    if dev_info.get("llm_error"):
        lines.append(f"*LLM Error:* {dev_info['llm_error']}")
    if dev_info.get("evidence_lines"):
        lines.append("*Evidence:* " + " | ".join(dev_info["evidence_lines"]))
    if not lines:
        return blocks
    import copy
    result_blocks = copy.copy(blocks)
    result_blocks.append({"type": "divider"})
    result_blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "\n".join(lines)}],
    })
    return result_blocks


def _post_loading_message(client: Any, channel: str) -> dict[str, Any] | None:
    try:
        response = client.chat_postMessage(
            channel=channel,
            text="...",
            blocks=ResponseFormatter.format_loading("..."),
        )
        if response.get("ok"):
            return response
    except Exception as error:
        logger.warning(f"Unable to post loading message: {error}")
    return None


def _start_loading_animation(
    client: Any,
    channel: str,
    placeholder: dict[str, Any] | None,
    interval_seconds: float = 0.45,
) -> Callable[[], None] | None:
    if not placeholder or not placeholder.get("ts"):
        return None

    stop_event = threading.Event()

    def _animate() -> None:
        frame_index = 0
        while not stop_event.wait(interval_seconds):
            frame_index = (frame_index + 1) % len(_LOADING_FRAMES)
            frame = _LOADING_FRAMES[frame_index]
            try:
                client.chat_update(
                    channel=channel,
                    ts=placeholder["ts"],
                    text=frame,
                    blocks=ResponseFormatter.format_loading(frame),
                )
            except Exception as error:
                logger.debug("Loading animation update skipped: %s", error)
                break

    thread = threading.Thread(target=_animate, name="loading-animation", daemon=True)
    thread.start()

    def _stop() -> None:
        stop_event.set()

    return _stop


def _finalize_response(
    client: Any,
    channel: str,
    placeholder: dict[str, Any] | None,
    text: str,
    blocks: list,
    stop_animation: Callable[[], None] | None = None,
) -> None:
    if stop_animation is not None:
        stop_animation()

    if placeholder and placeholder.get("ts"):
        try:
            client.chat_update(
                channel=channel,
                ts=placeholder["ts"],
                text=text,
                blocks=blocks,
            )
            return
        except Exception as error:
            logger.warning(f"Unable to update loading message: {error}")

    client.chat_postMessage(channel=channel, text=text, blocks=blocks)


def setup_handlers(app: App, settings: BotSettings) -> None:
    """Register all Slack event handlers"""

    # Initialize clients
    fastapi_client = FastAPIClient(
        settings.fastapi_base_url,
        settings.fastapi_api_key,
        timeout_seconds=settings.fastapi_timeout_seconds,
        max_retries=settings.fastapi_max_retries,
        retry_backoff_seconds=settings.fastapi_retry_backoff_seconds,
    )
    if settings.llm_provider == "ollama":
        llm_client = OllamaClient(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        logger.info(
            "Using local Ollama provider with model %s at %s",
            settings.ollama_model,
            settings.ollama_base_url,
        )
    else:
        llm_client = OpenRouterClient(
            settings.openrouter_api_key,
            settings.openrouter_models,
            settings.openrouter_base_url,
            settings.openrouter_site_url,
            settings.openrouter_site_name,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        logger.info("Using OpenRouter provider with %s model(s)", len(settings.openrouter_models))

    intent_parser = IntentParser(
        settings.openrouter_api_key,
        settings.openrouter_models,
        settings.openrouter_base_url,
        settings.openrouter_site_url,
        settings.openrouter_site_name,
        timeout_seconds=settings.llm_timeout_seconds,
        llm_client=llm_client,
    )
    formatter = ResponseFormatter()
    orchestrator = ConnectBotOrchestrator(
        llm_client=llm_client,
        fastapi_client=fastapi_client,
        intent_parser=intent_parser,
        formatter=formatter,
        history_window=settings.agent_history_window,
        llm_provider=settings.llm_provider,
        local_only_fail_closed=settings.local_only_fail_closed,
        bot_identity_name=settings.bot_identity_name,
        bot_scope_name=settings.bot_scope_name,
        bot_voice_style=settings.bot_voice_style,
        bot_personality_notes=settings.bot_personality_notes,
        social_deterministic_mode=settings.social_deterministic_mode,
        use_tool_router=settings.use_tool_router,
        sales_access_users=settings.sales_access_users,
    )

    def _conversation_id(channel: str, thread_ts: str | None = None) -> str:
        return f"{channel}:{thread_ts or 'root'}"

    @app.event("app_mention")
    def handle_app_mention(body, client, say, logger):
        """Handle when bot is mentioned in a channel"""
        placeholder = None
        stop_animation = None
        try:
            event = body["event"]
            channel = event["channel"]
            user_query = event["text"]
            # Remove the bot mention from the query
            user_query = user_query.split(">", 1)[-1].strip()

            dev_mode = _get_dev_mode(user_query)
            is_dev = dev_mode is not None
            is_dev_full = dev_mode == "dev_full"
            if is_dev:
                user_query = _strip_dev_prefix(user_query)
            logger.info(
                "Got mention from %s: %s (dev_mode=%s)",
                event["user"],
                user_query,
                dev_mode or "off",
            )
            if settings.use_agent_mode and not is_dev:
                social_result = orchestrator.try_social_response(user_query)
                if social_result is not None:
                    client.chat_postMessage(
                        channel=channel,
                        text=social_result.text,
                        blocks=social_result.blocks,
                    )
                    return
            placeholder = _post_loading_message(client, channel)
            stop_animation = _start_loading_animation(client, channel, placeholder)

            if settings.use_agent_mode:
                result = orchestrator.process_query(
                    _conversation_id(channel, event.get("thread_ts")),
                    user_query,
                    is_dev=is_dev,
                    user_id=event.get("user", ""),
                )
                final_blocks = _append_dev_blocks(result.blocks, result.dev_info) if is_dev else result.blocks
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    result.text,
                    final_blocks,
                    stop_animation=stop_animation,
                )
                if is_dev_full:
                    trace_thread_ts = event.get("thread_ts") or (placeholder or {}).get("ts")
                    _post_dev_full_trace_message(
                        client,
                        channel,
                        trace_thread_ts,
                        result.dev_info,
                    )
                return

            # Parse intent
            intent = intent_parser.parse(user_query)

            clarification = _build_intent_clarification(intent)
            if clarification is not None:
                text, blocks = clarification
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    text,
                    blocks,
                    stop_animation=stop_animation,
                )
                return

            if intent.get("endpoint") == "fastapi_healthcheck":
                text, blocks = _handle_fastapi_healthcheck_response(fastapi_client, formatter)
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    text,
                    blocks,
                    stop_animation=stop_animation,
                )
                return
            
            if intent.get("endpoint") in {"help", "chat"}:
                text, blocks = _handle_local_only_response(formatter, intent)
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    text,
                    blocks,
                    stop_animation=stop_animation,
                )
                return

            if not intent.get("endpoint"):
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    "I can't help with that yet.",
                    formatter.format_unsupported_query(),
                    stop_animation=stop_animation,
                )
                return

            # Call the appropriate API
            response_data = _call_api(fastapi_client, intent)

            if response_data is None:
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    f"Live data unavailable for {intent['endpoint']}.",
                    formatter.format_api_unavailable(intent["endpoint"]),
                    stop_animation=stop_animation,
                )
                return

            # Format and send response
            blocks = _format_response(formatter, intent["endpoint"], response_data)
            _finalize_response(
                client,
                channel,
                placeholder,
                formatter.to_text(intent["endpoint"], response_data),
                blocks,
                stop_animation=stop_animation,
            )

        except Exception as e:
            logger.error(f"Error handling mention: {e}")
            channel = body["event"].get("channel")
            if channel:
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    f"Error: {e}",
                    formatter.format_error(str(e)),
                    stop_animation=stop_animation,
                )
            else:
                if stop_animation is not None:
                    stop_animation()
                say(text=f"Error: {e}", blocks=formatter.format_error(str(e)))

    @app.event("message")
    def handle_direct_message(event, client, say, logger):
        """Handle direct messages to bot"""
        # Ignore bot messages, edits, and deletions
        if event.get("bot_id") or event.get("subtype"):
            return

        placeholder = None
        stop_animation = None
        try:
            channel = event["channel"]
            user_query = event.get("text", "").strip()
            if not user_query:
                return

            dev_mode = _get_dev_mode(user_query)
            is_dev = dev_mode is not None
            is_dev_full = dev_mode == "dev_full"
            if is_dev:
                user_query = _strip_dev_prefix(user_query)
            logger.info(
                "Got message from %s: %s (dev_mode=%s)",
                event.get("user", "unknown"),
                user_query,
                dev_mode or "off",
            )
            if settings.use_agent_mode and not is_dev:
                social_result = orchestrator.try_social_response(user_query)
                if social_result is not None:
                    client.chat_postMessage(
                        channel=channel,
                        text=social_result.text,
                        blocks=social_result.blocks,
                    )
                    return
            placeholder = _post_loading_message(client, channel)
            stop_animation = _start_loading_animation(client, channel, placeholder)

            if settings.use_agent_mode:
                result = orchestrator.process_query(
                    _conversation_id(channel, event.get("thread_ts")),
                    user_query,
                    is_dev=is_dev,
                    user_id=event.get("user", ""),
                )
                final_blocks = _append_dev_blocks(result.blocks, result.dev_info) if is_dev else result.blocks
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    result.text,
                    final_blocks,
                    stop_animation=stop_animation,
                )
                if is_dev_full:
                    trace_thread_ts = event.get("thread_ts") or (placeholder or {}).get("ts")
                    _post_dev_full_trace_message(
                        client,
                        channel,
                        trace_thread_ts,
                        result.dev_info,
                    )
                return

            # Parse intent
            intent = intent_parser.parse(user_query)

            clarification = _build_intent_clarification(intent)
            if clarification is not None:
                text, blocks = clarification
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    text,
                    blocks,
                    stop_animation=stop_animation,
                )
                return

            if intent.get("endpoint") == "fastapi_healthcheck":
                text, blocks = _handle_fastapi_healthcheck_response(fastapi_client, formatter)
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    text,
                    blocks,
                    stop_animation=stop_animation,
                )
                return
            
            if intent.get("endpoint") in {"help", "chat"}:
                text, blocks = _handle_local_only_response(formatter, intent)
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    text,
                    blocks,
                    stop_animation=stop_animation,
                )
                return

            if not intent.get("endpoint"):
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    "I can't help with that yet.",
                    formatter.format_unsupported_query(),
                    stop_animation=stop_animation,
                )
                return

            # Call the appropriate API
            response_data = _call_api(fastapi_client, intent)

            if response_data is None:
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    f"Live data unavailable for {intent['endpoint']}.",
                    formatter.format_api_unavailable(intent["endpoint"]),
                    stop_animation=stop_animation,
                )
                return

            # Format and send response
            blocks = _format_response(formatter, intent["endpoint"], response_data)
            _finalize_response(
                client,
                channel,
                placeholder,
                formatter.to_text(intent["endpoint"], response_data),
                blocks,
                stop_animation=stop_animation,
            )

        except Exception as e:
            logger.error(f"Error handling message: {e}", exc_info=True)
            channel = event.get("channel")
            if channel:
                _finalize_response(
                    client,
                    channel,
                    placeholder,
                    f"Error: {e}",
                    formatter.format_error(str(e)),
                    stop_animation=stop_animation,
                )
            else:
                if stop_animation is not None:
                    stop_animation()
                say(text=f"Error: {e}", blocks=formatter.format_error(str(e)))


def _call_api(client: FastAPIClient, intent: dict) -> dict | None:
    """Call the FastAPI endpoint based on parsed intent"""
    
    endpoint = intent.get("endpoint")
    params = intent.get("parameters", {})

    try:
        if endpoint == "backlog":
            return client.get_backlog()

        elif endpoint == "heading":
            return client.get_heading(
                head_name=params.get("head_name"),
                data_date=params.get("data_date")
            )

        elif endpoint == "heading_overall":
            return client.get_heading_overall(data_date=params.get("data_date"))

        elif endpoint == "livewire_demand":
            return client.get_livewire_demand(
                shortages_only=bool(params.get("shortages_only", False)),
                material_query=params.get("material_query"),
            )

        elif endpoint == "livewire_inventory":
            return client.get_livewire_inventory(location=params.get("location"))

        elif endpoint == "livewire_usage":
            return client.get_livewire_usage(dimension=params.get("dimension"))

        elif endpoint == "fastapi_healthcheck":
            return client.get_health()

        else:
            logger.warning(f"Unknown endpoint: {endpoint}")
            return None

    except Exception as e:
        logger.error(f"Error calling API endpoint {endpoint}: {e}")
        return None


def _format_response(formatter: ResponseFormatter, endpoint: str, data: dict) -> list:
    """Format API response based on endpoint type"""

    if endpoint == "backlog":
        return formatter.format_backlog(data)
    elif endpoint == "heading":
        return formatter.format_heading(data)
    elif endpoint == "livewire_demand":
        return formatter.format_livewire_demand(data)
    elif endpoint in {"heading_overall", "livewire_inventory", "livewire_usage"}:
        return formatter.format_chat(formatter.to_text(endpoint, data))
    elif endpoint == "help":
        return formatter.format_help()
    else:
        # Generic formatting for other endpoints
        return formatter.format_heading(data)
