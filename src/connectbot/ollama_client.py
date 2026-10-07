from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class OllamaClient:
    """Thin Ollama client using the OpenAI-compatible chat endpoint."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 20.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)

    def complete(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        response = self._client.post(
            f"{self.base_url}/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 700,
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as error:
            logger.error("Malformed Ollama response payload: %s", payload)
            raise RuntimeError("Malformed response from local Ollama server") from error

        return content, self.model
