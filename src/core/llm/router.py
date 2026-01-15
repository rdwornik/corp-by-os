"""
LLM Router - Intelligent routing between local and cloud.

Location: src/core/llm/router.py

Key principle: Sensitivity check ALWAYS runs locally first.
"""

import logging
from enum import Enum
from typing import Optional, Literal
from dataclasses import dataclass

from config.settings import get_settings

logger = logging.getLogger(__name__)


class Sensitivity(Enum):
    """Data sensitivity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    RESTRICTED = "restricted"


class Provider(Enum):
    """LLM providers."""
    OLLAMA = "ollama"
    CLAUDE = "claude"
    GEMINI = "gemini"


@dataclass
class RoutingDecision:
    """Result of routing decision."""
    provider: Provider
    model: str
    sensitivity: Sensitivity
    reason: str
    sanitized: bool = False


@dataclass
class LLMResponse:
    """Standardized response."""
    content: str
    provider: Provider
    model: str
    sensitivity: Sensitivity


class LLMRouter:
    """
    Routes LLM requests based on sensitivity and task.
    
    Flow:
    1. Check sensitivity LOCALLY (always)
    2. Route based on sensitivity + task requirements
    3. Sanitize if needed before cloud
    """
    
    def __init__(self):
        self.settings = get_settings()
        self._providers: dict[Provider, 'BaseLLMProvider'] = {}
        self._init_providers()
    
    def _init_providers(self):
        """Initialize available providers."""
        # Always init local
        from src.core.llm.providers.ollama import OllamaProvider
        self._providers[Provider.OLLAMA] = OllamaProvider(
            base_url=self.settings.ollama_base_url,
            model_fast=self.settings.ollama_model_fast,
            model_quality=self.settings.ollama_model_quality,
            model_embed=self.settings.ollama_model_embed
        )
        
        # Init cloud if keys available
        if self.settings.anthropic_api_key:
            from src.core.llm.providers.claude import ClaudeProvider
            self._providers[Provider.CLAUDE] = ClaudeProvider(
                api_key=self.settings.anthropic_api_key,
                model_fast=self.settings.claude_model_fast,
                model_quality=self.settings.claude_model_quality
            )
        
        if self.settings.google_api_key:
            from src.core.llm.providers.gemini import GeminiProvider
            self._providers[Provider.GEMINI] = GeminiProvider(
                api_key=self.settings.google_api_key,
                model_fast=self.settings.gemini_model_fast,
                model_quality=self.settings.gemini_model_quality
            )
    
    def check_sensitivity(self, text: str) -> Sensitivity:
        """
        Check sensitivity LOCALLY. Never sends to cloud.
        """
        from src.core.llm.sensitivity import SensitivityChecker
        checker = SensitivityChecker(self._providers[Provider.OLLAMA])
        return checker.check(text)
    
    def route(
        self,
        text: str,
        task: str = "general",
        quality: Literal["fast", "quality"] = "fast",
        force_local: bool = False
    ) -> RoutingDecision:
        """
        Decide which provider to use.
        """
        sensitivity = self.check_sensitivity(text)
        
        # RESTRICTED = always local
        if sensitivity == Sensitivity.RESTRICTED or force_local:
            return RoutingDecision(
                provider=Provider.OLLAMA,
                model=self.settings.ollama_model_quality if quality == "quality" else self.settings.ollama_model_fast,
                sensitivity=sensitivity,
                reason="Restricted/forced local"
            )
        
        # Tasks that prefer local
        local_tasks = {"sensitivity_check", "embeddings", "client_detection", "bulk_categorization"}
        if task in local_tasks:
            return RoutingDecision(
                provider=Provider.OLLAMA,
                model=self.settings.ollama_model_fast,
                sensitivity=sensitivity,
                reason=f"Task '{task}' prefers local"
            )
        
        # HIGH = local preferred, cloud with sanitization for quality
        if sensitivity == Sensitivity.HIGH:
            if quality == "quality" and self._get_cloud_provider():
                return RoutingDecision(
                    provider=self._get_cloud_provider(),
                    model=self._get_cloud_model(quality),
                    sensitivity=sensitivity,
                    reason="High sensitivity - cloud with sanitization",
                    sanitized=True
                )
            return RoutingDecision(
                provider=Provider.OLLAMA,
                model=self.settings.ollama_model_quality,
                sensitivity=sensitivity,
                reason="High sensitivity - local"
            )
        
        # MEDIUM/LOW = cloud OK
        cloud = self._get_cloud_provider()
        if cloud:
            return RoutingDecision(
                provider=cloud,
                model=self._get_cloud_model(quality),
                sensitivity=sensitivity,
                reason=f"Low/Medium sensitivity - {cloud.value}"
            )
        
        # Fallback
        return RoutingDecision(
            provider=Provider.OLLAMA,
            model=self.settings.ollama_model_fast,
            sensitivity=sensitivity,
            reason="Fallback to local"
        )
    
    def _get_cloud_provider(self) -> Optional[Provider]:
        """Get preferred cloud provider."""
        pref = Provider[self.settings.default_cloud_provider.upper()]
        if pref in self._providers:
            return pref
        # Fallback to any available
        for p in [Provider.CLAUDE, Provider.GEMINI]:
            if p in self._providers:
                return p
        return None
    
    def _get_cloud_model(self, quality: str) -> str:
        """Get model name for cloud provider."""
        prov = self._get_cloud_provider()
        if prov == Provider.CLAUDE:
            return self.settings.claude_model_quality if quality == "quality" else self.settings.claude_model_fast
        elif prov == Provider.GEMINI:
            return self.settings.gemini_model_quality if quality == "quality" else self.settings.gemini_model_fast
        return ""
    
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        task: str = "general",
        quality: Literal["fast", "quality"] = "fast",
        force_local: bool = False
    ) -> LLMResponse:
        """
        Generate with automatic routing.
        """
        decision = self.route(prompt, task, quality, force_local)
        provider = self._providers[decision.provider]
        
        # Sanitize if needed
        input_text = prompt
        if decision.sanitized:
            from src.core.llm.sensitivity import SensitivityChecker
            checker = SensitivityChecker(self._providers[Provider.OLLAMA])
            input_text = checker.sanitize(prompt)
        
        logger.info(f"Routing to {decision.provider.value}: {decision.reason}")
        
        content = provider.generate(input_text, system=system, quality=quality)
        
        return LLMResponse(
            content=content,
            provider=decision.provider,
            model=decision.model,
            sensitivity=decision.sensitivity
        )
    
    def embed(self, text: str) -> list[float]:
        """Generate embedding. Always local."""
        return self._providers[Provider.OLLAMA].embed(text)


# Singleton
_router: Optional[LLMRouter] = None

def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
