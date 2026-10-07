"""BotSettings.from_env: required values and parsing of list/flag settings."""

from __future__ import annotations

import pytest

from src.connectbot.config import BotSettings

_ENV_KEYS = [
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_APP_TOKEN",
    "USE_SOCKET_MODE",
    "CONNECTBOT_INTERNAL_API_TOKEN",
    "ENABLE_NOTIFICATION_BRIDGE",
    "SALES_ACCESS_USERS",
    "OPENROUTER_MODELS",
    "OPENROUTER_MODEL",
    "SLACK_NOTIFY_CHANNEL_DEFAULT",
    "SLACK_NOTIFY_CHANNEL_HEADING",
    "USE_TOOL_ROUTER",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-test")
    return monkeypatch


def test_bot_token_is_required(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.delenv("SLACK_BOT_TOKEN")
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        BotSettings.from_env()


def test_socket_mode_requires_app_token(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.delenv("SLACK_APP_TOKEN")
    with pytest.raises(ValueError, match="SLACK_APP_TOKEN"):
        BotSettings.from_env()


def test_http_mode_does_not_need_app_token(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.delenv("SLACK_APP_TOKEN")
    clean_env.setenv("USE_SOCKET_MODE", "false")
    assert BotSettings.from_env().use_socket_mode is False


def test_bridge_stays_off_without_token() -> None:
    assert BotSettings.from_env().notification_server_enabled is False


def test_bridge_turns_on_with_token(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CONNECTBOT_INTERNAL_API_TOKEN", "tok")
    assert BotSettings.from_env().notification_server_enabled is True


def test_sales_allowlist_is_trimmed(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SALES_ACCESS_USERS", " U1, ,U2 ")
    assert BotSettings.from_env().sales_access_users == {"U1", "U2"}


def test_openrouter_models_list(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("OPENROUTER_MODELS", "a/one, b/two,,")
    assert BotSettings.from_env().openrouter_models == ["a/one", "b/two"]


def test_openrouter_single_model_fallback() -> None:
    assert BotSettings.from_env().openrouter_models == ["openai/gpt-4o-mini"]


def test_only_configured_notify_channels_are_kept(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("SLACK_NOTIFY_CHANNEL_HEADING", "#heading")
    assert BotSettings.from_env().slack_notification_channels == {"heading": "#heading"}


def test_tool_router_defaults_on() -> None:
    assert BotSettings.from_env().use_tool_router is True
