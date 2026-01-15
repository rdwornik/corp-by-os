"""
Sensitivity Checker - Always runs locally.

Location: src/core/llm/sensitivity.py

CRITICAL: This never sends data to cloud APIs.
"""

import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.llm.providers.base import BaseLLMProvider

from src.core.llm.router import Sensitivity

logger = logging.getLogger(__name__)


class SensitivityChecker:
    """
    Checks content sensitivity LOCALLY.
    
    Levels:
    - RESTRICTED: Credentials, personal IDs, confidential docs
    - HIGH: Client details, financials, internal strategy
    - MEDIUM: General business, non-sensitive
    - LOW: Public info
    """
    
    # Patterns for RESTRICTED
    RESTRICTED_PATTERNS = [
        r"api[_-]?key",
        r"password",
        r"secret[_-]?key",
        r"access[_-]?token",
        r"bearer\s+[a-zA-Z0-9]",
        r"\b[A-Za-z0-9_-]{32,}\b",  # Long tokens
        r"ssh-rsa",
        r"-----BEGIN",
        r"confidential",
        r"internal[\s\-]only",
        r"do[\s\-]not[\s\-]share",
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",  # Credit card
    ]
    
    # Patterns for HIGH
    HIGH_PATTERNS = [
        r"salary",
        r"compensation",
        r"revenue\s*[\$€]\s*\d",
        r"profit\s*[\$€]\s*\d",
        r"\$\s*\d+\s*(m|million|k|thousand)",
        r"€\s*\d+\s*(m|million|k|thousand)",
        r"pricing",
        r"contract[\s\-]value",
        r"acquisition",
        r"merger",
        r"termination",
        r"performance[\s\-]review",
        r"hr[\s\-]issue",
        r"legal[\s\-]action",
    ]
    
    def __init__(self, local_llm: 'BaseLLMProvider'):
        self.local_llm = local_llm
        self._restricted = [re.compile(p, re.I) for p in self.RESTRICTED_PATTERNS]
        self._high = [re.compile(p, re.I) for p in self.HIGH_PATTERNS]
    
    def check(self, text: str) -> Sensitivity:
        """
        Check sensitivity. Pattern match first, LLM for complex cases.
        """
        # Fast pattern check
        for pattern in self._restricted:
            if pattern.search(text):
                logger.debug(f"RESTRICTED pattern: {pattern.pattern}")
                return Sensitivity.RESTRICTED
        
        for pattern in self._high:
            if pattern.search(text):
                logger.debug(f"HIGH pattern: {pattern.pattern}")
                return Sensitivity.HIGH
        
        # For longer text, use LLM
        if len(text) > 500:
            return self._llm_check(text)
        
        return Sensitivity.LOW
    
    def _llm_check(self, text: str) -> Sensitivity:
        """Use local LLM for nuanced detection."""
        prompt = f"""Classify this text's corporate data sensitivity.

Categories:
- RESTRICTED: Credentials, API keys, passwords, personal IDs, legal/HR confidential
- HIGH: Client financials, pricing, internal strategy, technical architecture details
- MEDIUM: General business, meeting logistics, non-sensitive discussions
- LOW: Public information, general knowledge

Text (first 1500 chars):
{text[:1500]}

Respond with ONLY: RESTRICTED, HIGH, MEDIUM, or LOW"""

        try:
            result = self.local_llm.generate(prompt, quality="fast")
            result = result.strip().upper()
            
            if "RESTRICTED" in result:
                return Sensitivity.RESTRICTED
            elif "HIGH" in result:
                return Sensitivity.HIGH
            elif "MEDIUM" in result:
                return Sensitivity.MEDIUM
            return Sensitivity.LOW
            
        except Exception as e:
            logger.warning(f"LLM check failed: {e}, defaulting to HIGH")
            return Sensitivity.HIGH  # Safe default
    
    def sanitize(self, text: str) -> str:
        """
        Sanitize text before sending to cloud.
        Masks sensitive patterns.
        """
        result = text
        
        # Mask tokens/keys
        result = re.sub(r'\b[A-Za-z0-9_-]{32,}\b', '[REDACTED_KEY]', result)
        
        # Mask SSN
        result = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED_ID]', result)
        
        # Mask credit cards
        result = re.sub(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[REDACTED_CC]', result)
        
        # Partial mask emails
        result = re.sub(r'(\w{2})\w*@(\w+)\.\w+', r'\1***@\2.***', result)
        
        # Mask phone numbers
        result = re.sub(r'\+?\d{1,3}[\s-]?\d{3}[\s-]?\d{3}[\s-]?\d{3,4}', '[REDACTED_PHONE]', result)
        
        return result
