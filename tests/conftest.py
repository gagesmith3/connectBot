"""Shared test doubles. Nothing here touches the network.

The FastAPI side uses the *real* FastAPIClient over an httpx.MockTransport, so
tests exercise the bot's actual request building; only the server is faked.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
import pytest

from src.connectbot.api_client import FastAPIClient
from src.connectbot.config import BotSettings
from src.connectbot.intent_parser import IntentParser
from src.connectbot.orchestrator import ConnectBotOrchestrator
from src.connectbot.response_formatter import ResponseFormatter

FROZEN_TODAY = date(2026, 10, 7)  # a Wednesday


class FakeChatLLM:
    """Scripted LLM without tool calling (like OllamaClient): the orchestrator
    disables its tool router for it. Each call pops the next scripted reply;
    an Exception reply is raised.
    """

    def __init__(self, replies: list[Any] | None = None, tool_replies: list[Any] | None = None):
        self.replies = list(replies or [])
        self.tool_replies = list(tool_replies or [])
        self.calls: list[list[dict[str, Any]]] = []
        self.tool_calls: list[dict[str, Any]] = []

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        self.calls.append(messages)
        if not self.replies:
            raise RuntimeError("FakeLLM.complete called with no scripted reply")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, "fake-model"


class FakeLLM(FakeChatLLM):
    """Scripted LLM with tool calling (like OpenRouterClient). `tool_replies` are
    message dicts, e.g. {"content": "...", "tool_calls": [...]}.
    """

    def complete_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], tool_choice: str | None = None
    ) -> tuple[dict[str, Any], str]:
        self.tool_calls.append(
            {
                "messages": [dict(m) for m in messages],
                "tools": [t["function"]["name"] for t in tools],
                "tool_choice": tool_choice,
            }
        )
        if not self.tool_replies:
            raise RuntimeError("FakeLLM.complete_with_tools called with no scripted reply")
        reply = self.tool_replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, "fake-tool-model"


class FakeFastAPIServer:
    """Path -> JSON body. A path mapped to None (or missing) answers 503."""

    def __init__(self, routes: dict[str, Any] | None = None):
        self.routes: dict[str, Any] = dict(routes or {})
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(503, json={"detail": "unavailable"})
        return httpx.Response(200, json=body)

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def client(self) -> FastAPIClient:
        # Build the client's httpx.Client on the mock transport from the start:
        # a default httpx.Client loads the OS certificate store (~0.3s each).
        real_client = httpx.Client

        def mock_client(**kwargs: Any) -> httpx.Client:
            return real_client(transport=httpx.MockTransport(self.handler), **kwargs)

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(httpx, "Client", mock_client)
            return FastAPIClient("http://fastapi.test", "test-key", max_retries=0, retry_backoff_seconds=0)


def make_settings(**overrides: Any) -> BotSettings:
    values: dict[str, Any] = {"slack_bot_token": "xoxb-test", "slack_signing_secret": "s" * 32}
    values.update(overrides)
    return BotSettings(**values)


@pytest.fixture
def fake_api() -> FakeFastAPIServer:
    return FakeFastAPIServer()


@pytest.fixture
def make_orchestrator(fake_api: FakeFastAPIServer) -> Callable[..., ConnectBotOrchestrator]:
    def _make(llm: FakeChatLLM | None = None, **kwargs: Any) -> ConnectBotOrchestrator:
        llm = llm or FakeLLM()
        api = fake_api.client()
        parser = IntentParser("", [], "", llm_client=llm)
        return ConnectBotOrchestrator(
            llm_client=llm, fastapi_client=api, intent_parser=parser, formatter=ResponseFormatter(), **kwargs
        )

    return _make


@pytest.fixture
def orch(make_orchestrator: Callable[..., ConnectBotOrchestrator]) -> ConnectBotOrchestrator:
    return make_orchestrator()


class _FrozenDate(date):
    @classmethod
    def today(cls) -> date:  # type: ignore[override]
        return FROZEN_TODAY


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch: pytest.MonkeyPatch) -> date:
    """Every test sees 2026-10-07 as today (date parsing and tool-router prompts).

    Patches `date` in every connectbot module that imported it, so the fixture
    survives code moving between modules.
    """
    for name, module in list(sys.modules.items()):
        if name.startswith("src.connectbot") and getattr(module, "date", None) is date:
            monkeypatch.setattr(module, "date", _FrozenDate)
    return FROZEN_TODAY


def tool_call(name: str, arguments: dict[str, Any] | str, call_id: str = "c1") -> dict[str, Any]:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
