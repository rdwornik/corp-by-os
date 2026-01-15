"""
Base LLM Provider - Abstract interface.

Location: src/core/llm/providers/base.py
"""

from abc import ABC, abstractmethod
from typing import Optional, Literal


class BaseLLMProvider(ABC):
    """Abstract base for LLM providers."""
    
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        quality: Literal["fast", "quality"] = "fast",
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> str:
        """Generate completion."""
        pass
    
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        quality: Literal["fast", "quality"] = "quality"
    ) -> str:
        """Chat completion."""
        pass
    
    def embed(self, text: str) -> list[float]:
        """Generate embedding. Override if supported."""
        raise NotImplementedError("Embeddings not supported")
