"""
DeepSeek API client — OpenAI-compatible REST via httpx.

Uses DeepSeek-V3 (deepseek-chat). No openai SDK required.
Same interface as SonnetClient: complete() returns str.

Requires DEEPSEEK_API_KEY in .env.
"""

import json
import logging
from typing import Optional, Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekClient:
    """
    Thin wrapper around DeepSeek REST API (OpenAI-compatible).

    Usage:
        client = DeepSeekClient()
        text = client.complete("Classify this file: invoice_2024.pdf")
    """

    def __init__(self):
        settings = get_settings()
        if not settings.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY not set in .env")
        self._model = settings.deepseek_model
        self._http = httpx.Client(
            base_url=DEEPSEEK_BASE_URL,
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            timeout=180.0,
        )

    @property
    def model_id(self) -> str:
        return self._model

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 8192,
        temperature: float = 0.0,
    ) -> str:
        """Send a prompt, return text response."""
        logger.debug("DeepSeek request: %d chars", len(prompt))

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = self._http.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 8192,
    ) -> Any:
        """Send prompt, parse and return JSON response."""
        json_system = (system or "") + "\nRespond with valid JSON only. No markdown, no explanation."
        text = self.complete(prompt, system=json_system.strip(), max_tokens=max_tokens, temperature=0.0)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("JSON parse failed. Response: %s", text)
            raise ValueError(f"Model did not return valid JSON: {e}") from e


_client: Optional[DeepSeekClient] = None


def get_client() -> DeepSeekClient:
    """Return shared DeepSeekClient instance."""
    global _client
    if _client is None:
        _client = DeepSeekClient()
    return _client
