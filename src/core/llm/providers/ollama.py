"""
Ollama Provider - Local LLM.

Location: src/core/llm/providers/ollama.py
"""

from typing import Optional, Literal
import httpx

from .base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    """Local Ollama provider."""
    
    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model_fast: str = "llama3.2",
        model_quality: str = "mistral",
        model_embed: str = "nomic-embed-text"
    ):
        self.base_url = base_url
        self.models = {
            "fast": model_fast,
            "quality": model_quality,
            "embed": model_embed
        }
        self._client: Optional[httpx.Client] = None
    
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=120.0
            )
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
        
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }
        if system:
            payload["system"] = system
        
        response = self.client.post("/api/generate", json=payload)
        response.raise_for_status()
        return response.json()["response"]
    
    def chat(
        self,
        messages: list[dict],
        quality: Literal["fast", "quality"] = "quality"
    ) -> str:
        model = self.models.get(quality, self.models["quality"])
        
        response = self.client.post("/api/chat", json={
            "model": model,
            "messages": messages,
            "stream": False
        })
        response.raise_for_status()
        return response.json()["message"]["content"]
    
    def embed(self, text: str) -> list[float]:
        response = self.client.post("/api/embeddings", json={
            "model": self.models["embed"],
            "prompt": text
        })
        response.raise_for_status()
        return response.json()["embedding"]
    
    def is_available(self) -> bool:
        """Check if Ollama is running."""
        try:
            response = self.client.get("/api/tags")
            return response.status_code == 200
        except Exception:
            return False
    
    def list_models(self) -> list[str]:
        """List available models."""
        try:
            response = self.client.get("/api/tags")
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except Exception:
            return []
