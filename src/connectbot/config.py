"""
Configuration management for Connect Bot
"""

from __future__ import annotations

import os


class BotSettings:
    """Settings for the Connect Bot, loaded from environment variables"""

    def __init__(
        self,
        slack_bot_token: str,
        slack_signing_secret: str,
        slack_app_token: str = "",
        internal_api_token: str = "",
        notification_server_enabled: bool = False,
        notification_server_host: str = "0.0.0.0",
        notification_server_port: int = 3010,
        slack_notification_channels: dict[str, str] | None = None,
        woocommerce_webhook_secret: str = "",
        woocommerce_order_admin_url_template: str = "",
        fastapi_base_url: str = "http://localhost:8100",
        fastapi_api_key: str = "",
        fastapi_timeout_seconds: float = 10.0,
        fastapi_max_retries: int = 2,
        fastapi_retry_backoff_seconds: float = 0.5,
        openrouter_api_key: str = "",
        openrouter_models: list[str] | None = None,
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        openrouter_site_url: str = "",
        openrouter_site_name: str = "Connect Bot",
        llm_provider: str = "openrouter",
        ollama_base_url: str = "http://127.0.0.1:11434",
        ollama_model: str = "gemma3:4b",
        bot_identity_name: str = "ConnectBot",
        bot_scope_name: str = "IWT / Connect operations",
        bot_voice_style: str = "concise, practical, direct, and shop-floor friendly",
        bot_personality_notes: str = "",
        social_deterministic_mode: bool = True,
        use_tool_router: bool = True,
        answer_spec_enabled: bool = True,
        sales_access_users: set[str] | None = None,
        llm_timeout_seconds: float = 20.0,
        local_only_fail_closed: bool = False,
        use_agent_mode: bool = True,
        agent_history_window: int = 6,
        log_level: str = "INFO",
        log_file: str = "logs/connectbot.log",
        use_socket_mode: bool = True,
        server_port: int = 3000,
        ssl_cert_file: str | None = None,
        ssl_key_file: str | None = None,
    ):
        self.slack_bot_token = slack_bot_token
        self.slack_signing_secret = slack_signing_secret
        self.slack_app_token = slack_app_token  # Required for Socket Mode
        self.internal_api_token = internal_api_token
        self.notification_server_enabled = notification_server_enabled
        self.notification_server_host = notification_server_host
        self.notification_server_port = notification_server_port
        self.slack_notification_channels = slack_notification_channels or {}
        self.woocommerce_webhook_secret = woocommerce_webhook_secret
        self.woocommerce_order_admin_url_template = woocommerce_order_admin_url_template
        self.fastapi_base_url = fastapi_base_url
        self.fastapi_api_key = fastapi_api_key
        self.fastapi_timeout_seconds = fastapi_timeout_seconds
        self.fastapi_max_retries = fastapi_max_retries
        self.fastapi_retry_backoff_seconds = fastapi_retry_backoff_seconds
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_models = openrouter_models or ["openai/gpt-4o-mini"]
        self.openrouter_base_url = openrouter_base_url.rstrip("/")
        self.openrouter_site_url = openrouter_site_url
        self.openrouter_site_name = openrouter_site_name
        self.llm_provider = llm_provider.strip().lower()
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.ollama_model = ollama_model
        self.bot_identity_name = bot_identity_name
        self.bot_scope_name = bot_scope_name
        self.bot_voice_style = bot_voice_style
        self.bot_personality_notes = bot_personality_notes
        self.social_deterministic_mode = social_deterministic_mode
        self.use_tool_router = use_tool_router
        self.answer_spec_enabled = answer_spec_enabled
        self.sales_access_users = sales_access_users or set()
        self.llm_timeout_seconds = llm_timeout_seconds
        self.local_only_fail_closed = local_only_fail_closed
        self.use_agent_mode = use_agent_mode
        self.agent_history_window = agent_history_window
        self.log_level = log_level
        self.log_file = log_file
        self.use_socket_mode = use_socket_mode
        self.server_port = server_port
        self.ssl_cert_file = ssl_cert_file
        self.ssl_key_file = ssl_key_file

    @classmethod
    def from_env(cls) -> BotSettings:
        """Load settings from environment variables"""

        # Required settings
        slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
        if not slack_bot_token:
            raise ValueError("SLACK_BOT_TOKEN environment variable is required")

        slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        if not slack_signing_secret:
            raise ValueError("SLACK_SIGNING_SECRET environment variable is required")

        # Optional settings
        slack_app_token = os.getenv("SLACK_APP_TOKEN", "")
        internal_api_token = os.getenv("CONNECTBOT_INTERNAL_API_TOKEN", "").strip()
        notification_requested = os.getenv("ENABLE_NOTIFICATION_BRIDGE", "true").lower() == "true"
        notification_server_enabled = notification_requested and bool(internal_api_token)
        notification_server_host = os.getenv("NOTIFICATION_SERVER_HOST", "0.0.0.0")
        notification_server_port = int(os.getenv("NOTIFICATION_SERVER_PORT", "3010"))
        slack_notification_channels = {
            key: value
            for key, value in {
                "default": os.getenv("SLACK_NOTIFY_CHANNEL_DEFAULT", "").strip(),
                "equipment": os.getenv("SLACK_NOTIFY_CHANNEL_EQUIPMENT", "").strip(),
                "livewire": os.getenv("SLACK_NOTIFY_CHANNEL_LIVEWIRE", "").strip(),
                "heading": os.getenv("SLACK_NOTIFY_CHANNEL_HEADING", "").strip(),
                "marketplace": os.getenv("SLACK_NOTIFY_CHANNEL_MARKETPLACE", "").strip(),
                "orders": os.getenv("SLACK_NOTIFY_CHANNEL_ORDERS", "").strip(),
                "ops": os.getenv("SLACK_NOTIFY_CHANNEL_OPS", "").strip(),
                "secondary": os.getenv("SLACK_NOTIFY_CHANNEL_SECONDARY", "").strip(),
            }.items()
            if value
        }
        woocommerce_webhook_secret = os.getenv("WOOCOMMERCE_WEBHOOK_SECRET", "").strip()
        woocommerce_order_admin_url_template = os.getenv(
            "WOOCOMMERCE_ORDER_ADMIN_URL_TEMPLATE",
            "",
        ).strip()
        fastapi_base_url = os.getenv("FASTAPI_BASE_URL", "http://localhost:8100")
        fastapi_api_key = os.getenv("FASTAPI_API_KEY", "")
        fastapi_timeout_seconds = float(os.getenv("FASTAPI_TIMEOUT_SECONDS", "10"))
        fastapi_max_retries = int(os.getenv("FASTAPI_MAX_RETRIES", "2"))
        fastapi_retry_backoff_seconds = float(os.getenv("FASTAPI_RETRY_BACKOFF_SECONDS", "0.5"))
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
        openrouter_models_raw = os.getenv("OPENROUTER_MODELS", "")
        openrouter_model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        openrouter_models = [model.strip() for model in openrouter_models_raw.split(",") if model.strip()]
        if not openrouter_models:
            openrouter_models = [openrouter_model]
        openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        openrouter_site_url = os.getenv("OPENROUTER_SITE_URL", "")
        openrouter_site_name = os.getenv("OPENROUTER_SITE_NAME", "Connect Bot")
        llm_provider = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ollama_model = os.getenv("OLLAMA_MODEL", "gemma3:4b")
        bot_identity_name = os.getenv("BOT_IDENTITY_NAME", "ConnectBot").strip() or "ConnectBot"
        bot_scope_name = os.getenv("BOT_SCOPE_NAME", "IWT / Connect operations").strip() or "IWT / Connect operations"
        bot_voice_style = (
            os.getenv(
                "BOT_VOICE_STYLE",
                "concise, practical, direct, and shop-floor friendly",
            ).strip()
            or "concise, practical, direct, and shop-floor friendly"
        )
        bot_personality_notes = os.getenv("BOT_PERSONALITY_NOTES", "").strip()
        social_deterministic_mode = os.getenv("SOCIAL_DETERMINISTIC_MODE", "true").lower() == "true"
        use_tool_router = os.getenv("USE_TOOL_ROUTER", "true").lower() == "true"
        answer_spec_enabled = os.getenv("ANSWER_SPEC_ENABLED", "true").lower() == "true"
        sales_access_users = {user.strip() for user in os.getenv("SALES_ACCESS_USERS", "").split(",") if user.strip()}
        llm_timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "20"))
        local_only_fail_closed = os.getenv("LOCAL_ONLY_FAIL_CLOSED", "false").lower() == "true"
        use_agent_mode = os.getenv("USE_AGENT_MODE", "true").lower() == "true"
        agent_history_window = int(os.getenv("AGENT_HISTORY_WINDOW", "6"))
        log_level = os.getenv("LOG_LEVEL", "INFO")
        log_file = os.getenv("LOG_FILE", "logs/connectbot.log")
        use_socket_mode = os.getenv("USE_SOCKET_MODE", "true").lower() == "true"
        server_port = int(os.getenv("SERVER_PORT", "3000"))
        ssl_cert_file = os.getenv("SSL_CERT_FILE")
        ssl_key_file = os.getenv("SSL_KEY_FILE")

        if use_socket_mode and not slack_app_token:
            raise ValueError("SLACK_APP_TOKEN is required when USE_SOCKET_MODE=true")

        return cls(
            slack_bot_token=slack_bot_token,
            slack_signing_secret=slack_signing_secret,
            slack_app_token=slack_app_token,
            internal_api_token=internal_api_token,
            notification_server_enabled=notification_server_enabled,
            notification_server_host=notification_server_host,
            notification_server_port=notification_server_port,
            slack_notification_channels=slack_notification_channels,
            woocommerce_webhook_secret=woocommerce_webhook_secret,
            woocommerce_order_admin_url_template=woocommerce_order_admin_url_template,
            fastapi_base_url=fastapi_base_url,
            fastapi_api_key=fastapi_api_key,
            fastapi_timeout_seconds=fastapi_timeout_seconds,
            fastapi_max_retries=fastapi_max_retries,
            fastapi_retry_backoff_seconds=fastapi_retry_backoff_seconds,
            openrouter_api_key=openrouter_api_key,
            openrouter_models=openrouter_models,
            openrouter_base_url=openrouter_base_url,
            openrouter_site_url=openrouter_site_url,
            openrouter_site_name=openrouter_site_name,
            llm_provider=llm_provider,
            ollama_base_url=ollama_base_url,
            ollama_model=ollama_model,
            bot_identity_name=bot_identity_name,
            bot_scope_name=bot_scope_name,
            bot_voice_style=bot_voice_style,
            bot_personality_notes=bot_personality_notes,
            social_deterministic_mode=social_deterministic_mode,
            use_tool_router=use_tool_router,
            answer_spec_enabled=answer_spec_enabled,
            sales_access_users=sales_access_users,
            llm_timeout_seconds=llm_timeout_seconds,
            local_only_fail_closed=local_only_fail_closed,
            use_agent_mode=use_agent_mode,
            agent_history_window=agent_history_window,
            log_level=log_level,
            log_file=log_file,
            use_socket_mode=use_socket_mode,
            server_port=server_port,
            ssl_cert_file=ssl_cert_file,
            ssl_key_file=ssl_key_file,
        )
