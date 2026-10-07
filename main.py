"""
connectBot — Slack bot for business operations queries

Connects to local FastAPI server to answer questions about:
- Lead times and ETAs
- Backlog status
- Production bottlenecks
- Heading/Trimmer metrics
- Manufacturing request states

Usage:
    cp .env.example .env        # fill in SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, etc.
    pip install -r requirements.txt
    python main.py
"""

from __future__ import annotations

import logging
import threading

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.connectbot.config import BotSettings
from src.connectbot.handlers import setup_handlers
from src.connectbot.notification_bridge import create_notification_app
from src.connectbot.utils.logging_setup import setup_logging

_settings: BotSettings = BotSettings.from_env()

# Initialize Slack app
slack_app = App(
    token=_settings.slack_bot_token,
    signing_secret=_settings.slack_signing_secret,
)

# Setup logging
setup_logging(_settings)
logger = logging.getLogger(__name__)

# Register all event handlers
setup_handlers(slack_app, _settings)


def _start_notification_bridge() -> threading.Thread | None:
    if not _settings.notification_server_enabled:
        if _settings.internal_api_token:
            logger.info("Notification bridge disabled by configuration")
        else:
            logger.info("Notification bridge disabled because CONNECTBOT_INTERNAL_API_TOKEN is not set")
        return None

    api = create_notification_app(_settings, slack_app.client)

    def _run() -> None:
        logger.info(
            "Starting Connect Bot notification bridge on %s:%s",
            _settings.notification_server_host,
            _settings.notification_server_port,
        )
        uvicorn.run(
            api,
            host=_settings.notification_server_host,
            port=_settings.notification_server_port,
            ssl_keyfile=_settings.ssl_key_file,
            ssl_certfile=_settings.ssl_cert_file,
            log_level="info",
        )

    thread = threading.Thread(target=_run, name="connectbot-notification-bridge", daemon=True)
    thread.start()
    return thread


def main():
    """Start the bot"""
    logger.info("Starting Connect Bot...")
    logger.info(f"FastAPI Server: {_settings.fastapi_base_url}")
    logger.info(f"Using event mode: {'Socket Mode' if _settings.use_socket_mode else 'HTTP Server'}")
    if _settings.llm_provider == "ollama":
        logger.info(
            "LLM provider: ollama (%s @ %s)",
            _settings.ollama_model,
            _settings.ollama_base_url,
        )
    else:
        logger.info("LLM provider: openrouter (%s model(s))", len(_settings.openrouter_models))

    if _settings.use_socket_mode:
        _start_notification_bridge()
        # Socket mode for local development
        handler = SocketModeHandler(slack_app, _settings.slack_app_token)
        handler.start()
    else:
        # HTTP server mode (for production with ngrok/tunnel)
        from slack_bolt.adapter.fastapi import SlackRequestHandler

        slack_handler = SlackRequestHandler(slack_app)
        api = create_notification_app(_settings, slack_app.client, slack_handler)

        logger.info(f"Starting HTTP server on 0.0.0.0:{_settings.server_port}")
        uvicorn.run(
            api,
            host="0.0.0.0",
            port=_settings.server_port,
            ssl_keyfile=_settings.ssl_key_file,
            ssl_certfile=_settings.ssl_cert_file,
        )


if __name__ == "__main__":
    main()
