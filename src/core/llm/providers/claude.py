"""
Claude Provider - Anthropic API.

Location: src/core/llm/providers/claude.py
"""

from typing import Optional, Literal

from .base import BaseLLMProvider


class ClaudeProvider(BaseLLMProvider):
    """Anthropic Claude provider."""
    
    def __init__(
        self,
        api_key: str,
        model_fast: str = "claude-3-5-haiku-20241022",
        model_quality: str = "claude-sonnet-4-20250514"
    ):
        self.api_key = api_key
        self.models = {
            "fast": model_fast,
            "quality": model_quality
        }
        self._client = None
    
    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client
    
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        quality: Literal["fast", "quality"] = "fast",
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> str:
        model = self.models.get(quality, self.models["fast"])
        
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature
        )
        return response.content[0].text
    
    def chat(
        self,
        messages: list[dict],
        quality: Literal["fast", "quality"] = "quality"
    ) -> str:
        model = self.models.get(quality, self.models["quality"])
        
        # Extract system if present
        system = ""
        chat_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
            else:
                chat_messages.append(msg)
        
        response = self.client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=chat_messages
        )
        return response.content[0].text
