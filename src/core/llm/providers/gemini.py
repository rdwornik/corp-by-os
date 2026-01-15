"""
Gemini Provider - Google API.

Location: src/core/llm/providers/gemini.py
"""

from typing import Optional, Literal

from .base import BaseLLMProvider


class GeminiProvider(BaseLLMProvider):
    """Google Gemini provider."""
    
    def __init__(
        self,
        api_key: str,
        model_fast: str = "gemini-2.0-flash-exp",
        model_quality: str = "gemini-1.5-pro"
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
            import google.generativeai as genai
            genai.configure(api_key=self.api_key)
            self._client = genai
        return self._client
    
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        quality: Literal["fast", "quality"] = "fast",
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> str:
        model_name = self.models.get(quality, self.models["fast"])
        model = self.client.GenerativeModel(model_name)
        
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        
        response = model.generate_content(
            full_prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens
            }
        )
        return response.text
    
    def chat(
        self,
        messages: list[dict],
        quality: Literal["fast", "quality"] = "quality"
    ) -> str:
        model_name = self.models.get(quality, self.models["quality"])
        model = self.client.GenerativeModel(model_name)
        
        chat = model.start_chat(history=[])
        
        result = ""
        for msg in messages:
            if msg["role"] == "user":
                response = chat.send_message(msg["content"])
                result = response.text
        
        return result
    
    def embed(self, text: str) -> list[float]:
        result = self.client.embed_content(
            model="models/embedding-001",
            content=text
        )
        return result["embedding"]
