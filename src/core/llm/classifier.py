"""
AIClassifier — provider-agnostic filename classification.

Uses PromptTemplate to load the prompt YAML and PromptLogger to record every
AI call. Implements a fallback chain so the best available provider is used.

Fallback chain (auto mode):
  1. deepseek  — DeepSeek-V3 via REST (cheapest, fast)
  2. haiku     — claude-haiku-4-5 via Anthropic SDK
  3. regex     — pure-regex heuristic (no API, lower quality)

Supported providers:
  "deepseek" — DeepSeek-V3 (deepseek-chat)
  "haiku"    — Anthropic claude-haiku-4-5
  "sonnet"   — Anthropic claude-sonnet-4 (legacy, still works)
  "regex"    — pure-regex heuristic (no API)

Usage:
    from src.core.llm.classifier import AIClassifier
    clf = AIClassifier()                  # deepseek → haiku → regex
    clf = AIClassifier(provider="haiku")  # haiku → regex
    results = clf.classify_filenames(filenames)
"""

import json
import logging
import re
import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from src.core.prompts import PromptTemplate, PromptLogger

logger = logging.getLogger(__name__)

PROMPT_NAME = "classify_presentation"

# Provider resolution order (most preferred first)
PROVIDER_CHAIN = ["deepseek", "haiku", "sonnet", "regex"]


# ---------------------------------------------------------------------------
# Pydantic result models
# ---------------------------------------------------------------------------

class ClassificationResult(BaseModel):
    """Structured result for a single filename."""
    original:    str
    client:      str | None = None
    date:        str | None = None
    ambig:       bool = False
    desc:        str = "Technical_Presentation"
    type:        Literal["PRES", "DOC", "REC"] = "PRES"
    confidence:  Literal["high", "medium", "low"] = "medium"
    parse_method: str = "unknown"


class PlanEntry(BaseModel):
    """One row in phase2_plan.json."""
    original:      str
    src:           str
    client:        str                        # "_Unknown" if not identified
    description:   str
    date:          str                        # always populated (mtime fallback)
    date_source:   Literal["filename", "mtime"]
    ambig:         bool = False
    type:          Literal["PRES", "DOC", "REC"] = "PRES"
    proposed_name: str
    dst:           str
    parse_method:  str
    confidence:    Literal["high", "medium", "low"] = "medium"
    status:        Literal["pending", "done", "skip", "exists", "cloud", "error"] = "pending"


# ---------------------------------------------------------------------------
# Regex fallback
# ---------------------------------------------------------------------------

_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_NOISE = {
    "blue", "yonder", "by", "saas", "technology", "technical", "platform",
    "integration", "architecture", "presentation", "overview", "session",
    "workshop", "rfp", "rfi", "demo", "discussion", "review", "summary",
    "slides", "slide", "deck", "followup", "follow", "solution", "discovery",
    "mike", "mikes", "local", "geller", "final", "copy", "updated", "template",
    "draft", "for", "and", "the", "with", "from", "to", "of", "at",
    "lp", "ep", "ms", "emea", "amer", "apac", "v1", "v2", "v3", "v4", "v5",
    "wms", "tms", "oms", "dms", "scpo", "ibp", "f", "r",
}

_TYPE_MAP = {
    ".pptx": "PRES", ".pptm": "PRES", ".ppt": "PRES",
    ".pdf":  "DOC",  ".docx": "DOC",  ".doc": "DOC",
    ".mp4":  "REC",  ".mkv":  "REC",  ".m4a": "REC",
}


def _to_pascal(text: str) -> str:
    return "_".join(p.capitalize() for p in re.split(r"[\s_-]+", text.strip()) if p)


def _regex_date(stem: str):
    s = stem
    mon = "|".join(_MONTHS.keys())

    m = re.search(r"(\d{4})[\s._-](\d{2})[\s._-](\d{2})", s)
    if m:
        y, mo, d = m.groups()
        if 2019 <= int(y) <= 2026 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}", False, s[:m.start()] + " " + s[m.end():]

    m = re.search(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        y, mo, d = m.groups()
        if 2019 <= int(y) <= 2026 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}", False, s[:m.start()] + " " + s[m.end():]

    m = re.search(r"(?<!\d)(\d{2})[_\-.](\\d{2})[_\-.](\\d{2})(?!\d)", s)
    if m:
        a, b, c = m.groups()
        if int(a) > 31:
            return f"20{a}-{b}-{c}", False, s[:m.start()] + " " + s[m.end():]
        if int(c) > 31:
            return f"20{c}-{b}-{a}", True, s[:m.start()] + " " + s[m.end():]
        return f"20{a}-{b}-{c}", True, s[:m.start()] + " " + s[m.end():]

    m = re.search(r"(\d{2})[.\-](\d{2})[.\-](\d{4})", s)
    if m:
        a, b, y = m.groups()
        if 2019 <= int(y) <= 2026:
            if int(a) > 12:
                return f"{y}-{b}-{a}", False, s[:m.start()] + " " + s[m.end():]
            if int(b) > 12:
                return f"{y}-{a}-{b}", False, s[:m.start()] + " " + s[m.end():]
            return f"{y}-{b}-{a}", True, s[:m.start()] + " " + s[m.end():]

    m = re.search(r"(\d{1,2})(" + mon + r")(\d{2,4})", s, re.IGNORECASE)
    if m:
        d, mn, yr = m.group(1), m.group(2).lower()[:3], m.group(3)
        mo = _MONTHS[mn]
        y = f"20{yr}" if len(yr) == 2 else yr
        if 2019 <= int(y) <= 2026 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d.zfill(2)}", False, s[:m.start()] + " " + s[m.end():]

    m = re.search(r"\b(" + mon + r")\w*\s+(?:\d{1,2}\w*\s+)?(\d{4})\b", s, re.IGNORECASE)
    if m:
        mo = _MONTHS[m.group(1).lower()[:3]]
        return f"{m.group(2)}-{mo}-01", False, s[:m.start()] + " " + s[m.end():]

    m = re.search(r"\b(\d{4})-(\d{2})\b", s)
    if m:
        y, mo = m.groups()
        if 2019 <= int(y) <= 2026 and 1 <= int(mo) <= 12:
            return f"{y}-{mo}-01", False, s[:m.start()] + " " + s[m.end():]

    return None, False, s


def _regex_client(stem: str) -> str | None:
    s = re.sub(r"^local[\s_]+", "", stem.strip(), flags=re.IGNORECASE)

    m = re.search(r"mike'?s\s+slides?\s+for\s+([A-Za-z][A-Za-z0-9&\s]+?)(?:\s*\d|\s*$)",
                  s, re.IGNORECASE)
    if m:
        return _to_pascal(m.group(1).strip())

    m = re.search(r"presentation\s+to\s+([A-Za-z][A-Za-z0-9&\s]+?)(?:\s|$)", s, re.IGNORECASE)
    if m:
        return _to_pascal(m.group(1).strip())

    m = re.search(r"\bby\s+(?:saas|platform|wms|tms|presentation)\s+([A-Za-z]\w+)",
                  s, re.IGNORECASE)
    if m:
        return m.group(1).capitalize()

    tokens = [t for t in re.split(r"[^A-Za-z0-9&]+", s) if t]
    good = [t for t in tokens if t.lower() not in _NOISE and not t.isdigit() and len(t) >= 2]
    if not good:
        return None
    return "_".join(t.capitalize() for t in good[:2])


def _regex_desc(stem: str) -> str:
    s = stem.lower()
    if re.search(r"\brfp\b", s):          return "RFP_Response"
    if re.search(r"\brfi\b|\brft\b", s):  return "RFI_Response"
    if re.search(r"\bworkshop\b", s):     return "Workshop"
    if re.search(r"\bdiscovery\b", s):    return "Discovery_Session"
    if re.search(r"\bintegration\b", s):  return "Integration_Workshop"
    if re.search(r"\barchitecture\b", s): return "Architecture_Review"
    if re.search(r"\bdeep.?dive\b", s):   return "Deep_Dive"
    if re.search(r"\bfollow.?up\b", s):   return "Follow_Up"
    if re.search(r"\bdemo\b", s):         return "Platform_Demo"
    if re.search(r"\btraining\b", s):     return "Training"
    return "Technical_Presentation"


def regex_classify(name: str) -> ClassificationResult:
    """Pure-regex classification, no API call."""
    stem = Path(name).stem
    date, ambig, stem_clean = _regex_date(stem)
    return ClassificationResult(
        original=name,
        client=_regex_client(stem_clean) or _regex_client(stem),
        date=date,
        ambig=ambig,
        desc=_regex_desc(stem),
        type=_TYPE_MAP.get(Path(name).suffix.lower(), "PRES"),
        confidence="low",
        parse_method="regex",
    )


# ---------------------------------------------------------------------------
# AIClassifier
# ---------------------------------------------------------------------------

class AIClassifier:
    """
    Provider-agnostic filename classifier with fallback chain.

    Fallback chain (when provider="deepseek"):
      deepseek → haiku → regex

    Loads the 'classify_presentation' prompt from YAML, sends filenames to
    the configured LLM, logs every call to prompt_history.jsonl.

    Args:
        provider:   Starting provider ("deepseek", "haiku", "sonnet", "regex").
                    Falls back down the chain if unavailable.
        log_path:   override default log path
        batch_size: filenames per API call (default 150)
    """

    BATCH_SIZE = 150

    def __init__(
        self,
        provider: str = "deepseek",
        log_path: Path | None = None,
        batch_size: int = BATCH_SIZE,
    ):
        self.batch_size = batch_size
        self.prompt = PromptTemplate.load(PROMPT_NAME)
        self.plog   = PromptLogger() if log_path is None else PromptLogger(log_path)

        self.provider = self._resolve_provider(provider)
        self._llm = None  # lazy-loaded

    # ------------------------------------------------------------------
    # Provider resolution
    # ------------------------------------------------------------------

    def _resolve_provider(self, requested: str) -> str:
        """
        Walk the fallback chain starting from `requested`.
        Returns the first provider whose client can be instantiated.
        """
        if requested == "regex":
            return "regex"

        # Build chain starting from requested
        start = PROVIDER_CHAIN.index(requested) if requested in PROVIDER_CHAIN else 0
        chain = PROVIDER_CHAIN[start:]

        for provider in chain:
            if provider == "regex":
                return "regex"
            if self._can_use_provider(provider):
                if provider != requested:
                    logger.warning(
                        "%s unavailable, falling back to %s", requested, provider
                    )
                return provider

        return "regex"

    def _can_use_provider(self, provider: str) -> bool:
        """Try to instantiate the provider's client. Return True if OK."""
        try:
            if provider == "deepseek":
                from src.core.llm.deepseek import get_client as _gc
                _gc()
                return True
            if provider == "haiku":
                from src.core.llm.haiku import get_client as _gc
                _gc()
                return True
            if provider == "sonnet":
                from src.core.llm.sonnet import get_client as _gc
                _gc()
                return True
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # LLM client
    # ------------------------------------------------------------------

    def _get_llm(self):
        if self._llm is None:
            if self.provider == "deepseek":
                from src.core.llm.deepseek import get_client
                self._llm = get_client()
            elif self.provider == "haiku":
                from src.core.llm.haiku import get_client
                self._llm = get_client()
            else:
                from src.core.llm.sonnet import get_client
                self._llm = get_client()
        return self._llm

    def _actual_model(self) -> str:
        if self.provider == "regex":
            return "regex"
        try:
            return self._get_llm().model_id
        except Exception:
            return self.prompt.model

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify_filenames(self, filenames: list[str]) -> list[ClassificationResult]:
        """
        Classify all filenames. Batches automatically.
        Returns list in same order as input.
        """
        if self.provider == "regex":
            return [regex_classify(n) for n in filenames]

        batches = [filenames[i:i+self.batch_size]
                   for i in range(0, len(filenames), self.batch_size)]
        results: list[ClassificationResult] = []

        for batch_num, batch in enumerate(batches, 1):
            print(f"  [{self.provider}] batch {batch_num}/{len(batches)} ({len(batch)} files)...")
            batch_results = self._classify_batch(batch, batch_num, len(batches))
            results.extend(batch_results)

        return results

    def _classify_batch(
        self, filenames: list[str], batch_num: int, total_batches: int
    ) -> list[ClassificationResult]:
        names_block = "\n".join(f"  {i+1:3}. {n}" for i, n in enumerate(filenames))
        rendered = self.prompt.render(n=len(filenames), filenames=names_block)

        raw_output = ""
        error_msg  = None

        try:
            llm = self._get_llm()
            raw_output = llm.complete(
                rendered,
                system="You parse presentation filenames. Output ONLY valid JSON array.",
                max_tokens=self.prompt.max_tokens,
                temperature=0.0,
            )
            parsed_list = self._parse_json_response(raw_output, filenames)

        except Exception as e:
            error_msg  = f"{type(e).__name__}: {e}"
            raw_output = error_msg
            logger.warning("Batch %d/%d failed (%s), falling back to regex",
                           batch_num, total_batches, error_msg)
            parsed_list = [regex_classify(n) for n in filenames]

        self.plog.log(
            prompt_name=self.prompt.name,
            prompt_version=self.prompt.version,
            model=self._actual_model(),
            provider=self.provider,
            rendered_prompt=rendered,
            raw_output=raw_output,
            batch_num=batch_num,
            batch_size=len(filenames),
            error=error_msg,
        )

        return parsed_list

    def _parse_json_response(
        self, raw: str, filenames: list[str]
    ) -> list[ClassificationResult]:
        """Parse JSON response; fall back to regex for entries that are malformed."""
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("JSON parse failed: %s\nRaw: %.200s", e, raw)
            return [regex_classify(n) for n in filenames]

        if isinstance(data, dict):
            lists = [v for v in data.values() if isinstance(v, list)]
            data = lists[0] if lists else []
        if not isinstance(data, list):
            data = []

        results = []
        for i, name in enumerate(filenames):
            meta = data[i] if i < len(data) and isinstance(data[i], dict) else {}
            results.append(ClassificationResult(
                original=name,
                client=meta.get("client") or None,
                date=meta.get("date") or None,
                ambig=bool(meta.get("ambig", False)),
                desc=meta.get("desc") or "Technical_Presentation",
                type=meta.get("type") or _TYPE_MAP.get(Path(name).suffix.lower(), "PRES"),
                confidence=meta.get("confidence") or "medium",
                parse_method=self.provider,
            ))
        return results
