"""
Sonnet API client — single LLM entry point.

Location: src/core/llm/sonnet.py

No routing, no sensitivity check. Uses Claude Sonnet 4 for all tasks.
"""

import json
import logging
from typing import Any

import anthropic

from config.settings import get_settings

logger = logging.getLogger(__name__)


class SonnetClient:
    """
    Thin wrapper around Anthropic SDK.

    Usage:
        client = SonnetClient()
        text = client.complete("Classify this file: invoice_2024.pdf")
        data = client.complete_json(
            "Return JSON with name and date",
            schema={"name": str, "date": str},
        )
    """

    def __init__(self):
        settings = get_settings()
        self._model = settings.claude_model
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    @property
    def model_id(self) -> str:
        return self._model

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Send a prompt, return text response."""
        logger.debug(f"Sonnet request: {len(prompt)} chars")

        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> Any:
        """
        Send a prompt, parse and return JSON response.

        Adds JSON instruction to system prompt automatically.
        Raises ValueError if response is not valid JSON.
        """
        json_system = (
            system or ""
        ) + "\nRespond with valid JSON only. No markdown, no explanation."

        text = self.complete(
            prompt, system=json_system.strip(), max_tokens=max_tokens, temperature=0.0
        )

        # Strip markdown code fences if model adds them
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed. Response was:\n{text}")
            raise ValueError(f"Model did not return valid JSON: {e}") from e


# Module-level singleton
_client: SonnetClient | None = None


def get_client() -> SonnetClient:
    """Return shared SonnetClient instance."""
    global _client
    if _client is None:
        _client = SonnetClient()
    return _client
