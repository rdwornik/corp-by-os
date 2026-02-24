"""
Haiku API client — Anthropic claude-haiku-4-5 (fast, cheap fallback).

Same interface as SonnetClient. Used as second-tier fallback after DeepSeek.
Requires ANTHROPIC_API_KEY in .env.
"""

import logging
from typing import Optional

import anthropic

from config.settings import get_settings

logger = logging.getLogger(__name__)


class HaikuClient:
    """
    Thin wrapper around Anthropic SDK using the Haiku model.

    Usage:
        client = HaikuClient()
        text = client.complete("Classify this file: invoice_2024.pdf")
    """

    def __init__(self):
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set in .env")
        self._model = settings.haiku_model
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

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
        logger.debug("Haiku request: %d chars", len(prompt))
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


_client: Optional[HaikuClient] = None


def get_client() -> HaikuClient:
    """Return shared HaikuClient instance."""
    global _client
    if _client is None:
        _client = HaikuClient()
    return _client
