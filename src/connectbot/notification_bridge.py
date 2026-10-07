"""Notification bridge for forwarding internal alerts and public WooCommerce events through Connect Bot."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from slack_sdk.errors import SlackApiError
from slack_sdk.web import WebClient

from .config import BotSettings

logger = logging.getLogger(__name__)


class NotificationRequest(BaseModel):
    webhook_key: str = Field(default="default")
    payload: dict[str, Any] = Field(default_factory=dict)


def _post_message(
    slack_client: WebClient,
    channel: str,
    text: str,
    blocks: list[dict[str, Any]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message_args: dict[str, Any] = {
        "channel": channel,
        "text": text,
    }

    if blocks:
        message_args["blocks"] = blocks
    if attachments:
        message_args["attachments"] = attachments

    return slack_client.chat_postMessage(**message_args)


def _build_wc_customer_name(order: dict[str, Any]) -> str:
    billing = order.get("billing") if isinstance(order.get("billing"), dict) else {}
    first = str(billing.get("first_name") or "").strip()
    last = str(billing.get("last_name") or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full

    company = str(billing.get("company") or "").strip()
    if company:
        return company

    return "Unknown customer"


def _build_wc_items(order: dict[str, Any]) -> list[str]:
    items: list[str] = []
    line_items = order.get("line_items")
    if not isinstance(line_items, list):
        return items

    for item in line_items:
        if not isinstance(item, dict):
            continue
        quantity = int(item.get("quantity") or 0)
        name = str(item.get("name") or "Item").strip() or "Item"
        items.append(f"{quantity}x {name}" if quantity > 0 else name)

    return items


def _build_wc_order_url(settings: BotSettings, order_id: int) -> str:
    template = settings.woocommerce_order_admin_url_template.strip()
    if not template or order_id <= 0:
        return ""
    try:
        return template % order_id
    except Exception:
        logger.warning("Invalid WOOCOMMERCE_ORDER_ADMIN_URL_TEMPLATE: %s", template)
        return ""


def _format_wc_message(settings: BotSettings, order: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    order_id = int(order.get("id") or 0)
    order_number = str(order.get("number") or order_id or "unknown").strip() or "unknown"
    status = str(order.get("status") or "unknown").strip() or "unknown"
    currency = str(order.get("currency") or "USD").strip() or "USD"
    total = str(order.get("total") or "0.00").strip() or "0.00"
    customer_name = _build_wc_customer_name(order)
    payment_method = str(order.get("payment_method_title") or "").strip()
    created_at = str(order.get("date_created") or "").strip()
    items = _build_wc_items(order)
    billing = order.get("billing") if isinstance(order.get("billing"), dict) else {}
    customer_email = str(billing.get("email") or "").strip()
    order_url = _build_wc_order_url(settings, order_id)

    text_lines = [
        "New Marketplace Order",
        f"Order #{order_number}",
        f"Customer: {customer_name}",
        f"Total: {currency} {total}",
        f"Status: {status}",
    ]
    if payment_method:
        text_lines.append(f"Payment: {payment_method}")
    if items:
        text_lines.append("Items: " + "; ".join(items))
    if order_url:
        text_lines.append(f"Order Link: {order_url}")
    text = "\n".join(text_lines)

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "New Marketplace Order",
                "emoji": True,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Order*\n#{order_number}"},
                {"type": "mrkdwn", "text": f"*Status*\n{status}"},
                {"type": "mrkdwn", "text": f"*Customer*\n{customer_name}"},
                {"type": "mrkdwn", "text": f"*Total*\n{currency} {total}"},
            ],
        },
    ]

    if items:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Items*\n• " + "\n• ".join(items),
                },
            }
        )

    context_parts: list[str] = []
    if customer_email:
        context_parts.append(f"Email: {customer_email}")
    if payment_method:
        context_parts.append(f"Payment: {payment_method}")
    if created_at:
        context_parts.append(f"Created: {created_at}")
    if order_url:
        context_parts.append(f"<{order_url}|Open order in WooCommerce>")
    if context_parts:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": " • ".join(context_parts),
                    }
                ],
            }
        )

    return text, blocks


def _resolve_channel(settings: BotSettings, webhook_key: str) -> str:
    key = (webhook_key or "default").strip() or "default"
    return settings.slack_notification_channels.get(key) or settings.slack_notification_channels.get("default", "")


def create_notification_app(
    settings: BotSettings,
    slack_client: WebClient,
    slack_request_handler: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="Connect Bot Notification Bridge")

    @app.get("/internal/health")
    async def internal_health() -> dict[str, Any]:
        return {
            "ok": True,
            "notification_bridge_enabled": settings.notification_server_enabled,
        }

    @app.post("/internal/slack/notify")
    async def post_internal_notification(
        request: NotificationRequest,
        x_connectbot_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not settings.notification_server_enabled:
            raise HTTPException(status_code=503, detail="Notification bridge is disabled")

        if not settings.internal_api_token:
            raise HTTPException(status_code=503, detail="Notification bridge token is not configured")

        if x_connectbot_token != settings.internal_api_token:
            raise HTTPException(status_code=401, detail="Invalid ConnectBot bridge token")

        channel = _resolve_channel(settings, request.webhook_key)
        if not channel:
            raise HTTPException(
                status_code=500,
                detail=f"No Slack channel configured for webhook_key '{request.webhook_key or 'default'}'",
            )

        payload = request.payload or {}
        text = str(payload.get("text") or "Notification from IWT")
        blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else None
        attachments = payload.get("attachments") if isinstance(payload.get("attachments"), list) else None

        try:
            response = _post_message(slack_client, channel, text, blocks=blocks, attachments=attachments)
        except SlackApiError as error:
            detail = error.response.get("error", str(error)) if error.response else str(error)
            logger.error("Slack bridge post failed for key %s: %s", request.webhook_key, detail)
            raise HTTPException(status_code=502, detail=f"Slack API error: {detail}") from error
        except Exception as error:  # pragma: no cover
            logger.error("Unexpected Slack bridge error for key %s: %s", request.webhook_key, error)
            raise HTTPException(status_code=502, detail=str(error)) from error

        return {
            "ok": bool(response.get("ok")),
            "channel": channel,
            "ts": response.get("ts"),
        }

    @app.get("/webhooks/woocommerce")
    async def woocommerce_ready() -> dict[str, Any]:
        return {
            "ok": True,
            "ready": True,
            "integration": "woocommerce",
        }

    @app.post("/webhooks/woocommerce")
    async def woocommerce_webhook(
        req: Request,
        x_wc_webhook_signature: str | None = Header(default=None),
        x_wc_webhook_topic: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_body = await req.body()
        topic = (x_wc_webhook_topic or "").strip()

        if not raw_body or not raw_body.strip():
            return {
                "ok": True,
                "ignored": True,
                "reason": "empty_body",
                "topic": topic,
            }

        if topic != "order.created":
            return {
                "ok": True,
                "ignored": True,
                "topic": topic,
            }

        secret = settings.woocommerce_webhook_secret.strip()
        if not secret:
            raise HTTPException(status_code=503, detail="WooCommerce webhook secret is not configured")

        signature = (x_wc_webhook_signature or "").strip()
        if not signature:
            raise HTTPException(status_code=401, detail="Missing WooCommerce signature")

        expected_signature = base64.b64encode(
            hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        ).decode("utf-8")
        if not hmac.compare_digest(expected_signature, signature):
            raise HTTPException(status_code=401, detail="Invalid WooCommerce signature")

        try:
            order = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from error

        if not isinstance(order, dict):
            raise HTTPException(status_code=400, detail="WooCommerce payload must be a JSON object")

        channel = _resolve_channel(settings, "marketplace")
        if not channel:
            raise HTTPException(status_code=500, detail="No Slack channel configured for webhook_key 'marketplace'")

        text, blocks = _format_wc_message(settings, order)
        try:
            response = _post_message(slack_client, channel, text, blocks=blocks)
        except SlackApiError as error:
            detail = error.response.get("error", str(error)) if error.response else str(error)
            logger.error("WooCommerce Slack post failed: %s", detail)
            raise HTTPException(status_code=502, detail=f"Slack API error: {detail}") from error
        except Exception as error:  # pragma: no cover
            logger.error("Unexpected WooCommerce webhook error: %s", error)
            raise HTTPException(status_code=502, detail=str(error)) from error

        order_id = int(order.get("id") or 0)
        order_number = str(order.get("number") or order_id or "unknown").strip() or "unknown"
        return {
            "ok": bool(response.get("ok")),
            "channel": channel,
            "ts": response.get("ts"),
            "topic": topic,
            "order_id": order_id,
            "order_number": order_number,
        }

    if slack_request_handler is not None:
        @app.post("/slack/events")
        async def slack_events(req: Request):
            return await slack_request_handler.handle(req)

    return app