from __future__ import annotations

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Reasoning models (gpt-oss, nemotron, qwen, etc.) may emit chain-of-thought
# inline in the content. Strip any <think>/<thinking> blocks defensively,
# including an unterminated block at the start of the response.
_THINK_BLOCK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"^\s*<think(?:ing)?>.*", re.DOTALL | re.IGNORECASE)
# gpt-oss (harmony format) sometimes leaks its channel marker glued to the
# answer: "finalWe've produced..." / "assistantfinalSales are...".
_FINAL_MARKER_RE = re.compile(r"^\s*(?:assistant)?final\s*(?=[A-Z0-9\"'*_•(\[-])")
# It can also leak the whole analysis channel: "analysisWe need ...". Only the
# text after a glued final marker is an answer.
_ANALYSIS_OPEN_RE = re.compile(r"^\s*analysis(?=[A-Z])")
_GLUED_FINAL_RE = re.compile(r"(?:assistant)?final(?=[A-Z0-9\"'*_•(\[-])")
# glm (and others) can write tool calls into the content as markup instead of
# real tool_calls: "<tool_call>heading<arg_key>…</tool_call>". Never an answer.
_TOOL_CALL_MARKUP_RE = re.compile(r"<tool_call>.*?(?:</tool_call>|$)", re.DOTALL)


def _strip_reasoning(content: str) -> str:
    cleaned = _TOOL_CALL_MARKUP_RE.sub("", _THINK_BLOCK_RE.sub("", content))
    if _THINK_OPEN_RE.match(cleaned):
        # Unterminated think block with no answer after it — nothing usable.
        return ""
    if _ANALYSIS_OPEN_RE.match(cleaned):
        finals = list(_GLUED_FINAL_RE.finditer(cleaned))
        # No final answer after the leaked reasoning — nothing usable.
        return cleaned[finals[-1].end() :].strip() if finals else ""
    cleaned = _FINAL_MARKER_RE.sub("", cleaned)
    return cleaned.strip()


class OpenRouterClient:
    """Thin OpenRouter client with ordered model fallback."""

    def __init__(
        self,
        api_key: str,
        models: list[str],
        base_url: str,
        site_url: str = "",
        site_name: str = "Connect Bot",
        timeout_seconds: float = 20.0,
    ):
        self.api_key = api_key
        self.models = models
        self.base_url = base_url.rstrip("/")
        self.site_url = site_url
        self.site_name = site_name
        self._client = httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.site_name:
            headers["X-OpenRouter-Title"] = self.site_name
        return headers

    def _supports_system_prompt(self, error: httpx.HTTPStatusError) -> bool:
        try:
            payload = error.response.json()
        except ValueError:
            return True

        message = json.dumps(payload).lower()
        unsupported_markers = [
            "developer instruction is not enabled",
            "system message",
            "system role",
            "unsupported_value",
            "invalid_argument",
        ]
        return not any(marker in message for marker in unsupported_markers)

    def _flatten_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        lines: list[str] = []
        for message in messages:
            role = message["role"].capitalize()
            content = message["content"].strip()
            lines.append(f"{role}: {content}")
        lines.append("Assistant: Respond to the user based on the conversation above.")
        return [{"role": "user", "content": "\n\n".join(lines)}]

    def _request_message(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> dict:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 1500,
            # Keep chain-of-thought out of the response and small in the
            # token budget (reasoning tokens count against max_tokens).
            "reasoning": {"effort": "low", "exclude": True},
        }
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice

        response = self._client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        message = payload["choices"][0]["message"]
        message["content"] = _strip_reasoning(message.get("content") or "")
        if not message["content"] and not message.get("tool_calls"):
            # Reasoning model burned the budget without a final answer, or the
            # provider put everything in the reasoning field. Treat as failure
            # so the fallback chain tries the next model.
            raise RuntimeError(f"{model} returned no answer content")
        return message

    def _request(self, messages: list[dict[str, str]], model: str) -> str:
        content = self._request_message(messages, model)["content"]
        if not content:
            raise RuntimeError(f"{model} returned no answer content")
        return content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | None = None,
    ) -> tuple[dict, str]:
        """Single tool-capable completion turn with the ordered model fallback.

        Returns the full assistant message (content and/or tool_calls) plus the
        model that produced it. The caller owns the tool loop.
        """
        last_error: Exception | None = None
        for model in self.models:
            try:
                logger.info("Trying OpenRouter model (tools): %s", model)
                message = self._request_message(messages, model, tools=tools, tool_choice=tool_choice)
                logger.info("OpenRouter model succeeded (tools): %s", model)
                return message, model
            except Exception as error:
                last_error = error
                logger.warning("OpenRouter model %s failed (tools): %s", model, error)

        if last_error is not None:
            raise last_error
        raise RuntimeError("No OpenRouter models configured")

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        last_error: Exception | None = None
        for model in self.models:
            try:
                logger.info("Trying OpenRouter model: %s", model)
                content = self._request(messages, model)
                logger.info("OpenRouter model succeeded: %s", model)
                return content, model
            except httpx.HTTPStatusError as error:
                if not self._supports_system_prompt(error):
                    try:
                        logger.info(
                            "Retrying OpenRouter model without system prompt: %s",
                            model,
                        )
                        content = self._request(self._flatten_messages(messages), model)
                        logger.info(
                            "OpenRouter model succeeded without system prompt: %s",
                            model,
                        )
                        return content, model
                    except Exception as retry_error:
                        last_error = retry_error
                        logger.warning(
                            "OpenRouter model %s failed again without system prompt: %s",
                            model,
                            retry_error,
                        )
                        continue

                last_error = error
                logger.warning(
                    "OpenRouter model %s failed with status %s: %s",
                    model,
                    error.response.status_code,
                    error.response.text[:300],
                )
            except Exception as error:
                last_error = error
                logger.warning("OpenRouter model %s failed: %s", model, error)

        if last_error is not None:
            raise last_error
        raise RuntimeError("No OpenRouter models configured")
