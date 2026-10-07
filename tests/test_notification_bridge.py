"""Notification bridge HTTP surface: token auth, channel routing, WooCommerce HMAC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from slack_sdk.errors import SlackApiError

from src.connectbot.notification_bridge import create_notification_app
from tests.conftest import make_settings

TOKEN = "bridge-token"
WC_SECRET = "wc-secret"
CHANNELS = {"default": "#all", "heading": "#heading", "marketplace": "#marketplace"}


class FakeSlack:
    def __init__(self, error: str | None = None):
        self.posts: list[dict[str, Any]] = []
        self.error = error

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        if self.error:
            raise SlackApiError("boom", {"error": self.error})
        self.posts.append(kwargs)
        return {"ok": True, "ts": "123.456"}


def _client(slack: FakeSlack | None = None, **overrides: Any) -> tuple[TestClient, FakeSlack]:
    slack = slack or FakeSlack()
    values: dict[str, Any] = {
        "internal_api_token": TOKEN,
        "notification_server_enabled": True,
        "slack_notification_channels": dict(CHANNELS),
        "woocommerce_webhook_secret": WC_SECRET,
    }
    values.update(overrides)
    return TestClient(create_notification_app(make_settings(**values), slack)), slack  # type: ignore[arg-type]


def _notify(client: TestClient, key: str = "heading", token: str | None = TOKEN) -> Any:
    headers = {"X-ConnectBot-Token": token} if token is not None else {}
    return client.post(
        "/internal/slack/notify", json={"webhook_key": key, "payload": {"text": "hello"}}, headers=headers
    )


def _wc_sign(body: bytes, secret: str = WC_SECRET) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


ORDER = json.dumps(
    {"id": 42, "number": "1042", "total": "99.00", "billing": {"first_name": "Ada", "last_name": "L"}}
).encode()


# --- internal notify ---------------------------------------------------------


def test_health() -> None:
    client, _ = _client()
    assert client.get("/internal/health").json() == {"ok": True, "notification_bridge_enabled": True}


def test_notify_posts_to_the_keyed_channel() -> None:
    client, slack = _client()
    response = _notify(client, "heading")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "channel": "#heading", "ts": "123.456"}
    assert slack.posts == [{"channel": "#heading", "text": "hello"}]


def test_unknown_key_falls_back_to_default_channel() -> None:
    client, slack = _client()
    _notify(client, "nope")
    assert slack.posts[0]["channel"] == "#all"


def test_unknown_key_without_default_is_500() -> None:
    client, _ = _client(slack_notification_channels={"heading": "#heading"})
    assert _notify(client, "nope").status_code == 500


@pytest.mark.parametrize("token", ["wrong", None])
def test_bad_or_missing_token_is_401(token: str | None) -> None:
    client, slack = _client()
    assert _notify(client, token=token).status_code == 401
    assert slack.posts == []


def test_disabled_bridge_is_503() -> None:
    client, _ = _client(notification_server_enabled=False)
    assert _notify(client).status_code == 503


def test_slack_error_is_502() -> None:
    client, _ = _client(FakeSlack(error="channel_not_found"))
    response = _notify(client)
    assert response.status_code == 502
    assert "channel_not_found" in response.text


# --- WooCommerce webhook -----------------------------------------------------


def _wc_post(client: TestClient, body: bytes, signature: str | None, topic: str = "order.created") -> Any:
    headers = {"X-WC-Webhook-Topic": topic}
    if signature is not None:
        headers["X-WC-Webhook-Signature"] = signature
    return client.post("/webhooks/woocommerce", content=body, headers=headers)


def test_valid_order_posts_to_marketplace() -> None:
    client, slack = _client()
    response = _wc_post(client, ORDER, _wc_sign(ORDER))
    assert response.status_code == 200
    assert response.json()["order_number"] == "1042"
    assert slack.posts[0]["channel"] == "#marketplace"
    assert "New Marketplace Order" in slack.posts[0]["text"]
    assert "Ada L" in slack.posts[0]["text"]


@pytest.mark.parametrize("signature", [None, "", _wc_sign(ORDER, "other-secret")])
def test_missing_or_bad_signature_is_401(signature: str | None) -> None:
    client, slack = _client()
    assert _wc_post(client, ORDER, signature).status_code == 401
    assert slack.posts == []


def test_other_topics_are_ignored() -> None:
    client, slack = _client()
    response = _wc_post(client, ORDER, None, topic="order.updated")
    assert response.json()["ignored"] is True
    assert slack.posts == []


def test_empty_body_is_ignored() -> None:
    client, _ = _client()
    assert _wc_post(client, b"", None).json()["reason"] == "empty_body"


def test_unconfigured_secret_is_503() -> None:
    client, _ = _client(woocommerce_webhook_secret="")
    assert _wc_post(client, ORDER, _wc_sign(ORDER)).status_code == 503


def test_signed_invalid_json_is_400() -> None:
    client, _ = _client()
    body = b"{not json"
    assert _wc_post(client, body, _wc_sign(body)).status_code == 400
