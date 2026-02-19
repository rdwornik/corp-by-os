"""
Prompt management utilities for Corporate OS.

PromptTemplate  — loads a versioned YAML prompt, renders it with variables.
PromptLogger    — appends every AI call to logs/prompt_history.jsonl.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Directory that holds all *.yaml prompt files
PROMPTS_DIR = Path(__file__).parent

# Default log path (relative to repo root)
DEFAULT_LOG_PATH = Path(__file__).parent.parent.parent.parent / "logs" / "prompt_history.jsonl"


class PromptTemplate:
    """
    Loads a YAML prompt definition and renders it with named variables.

    YAML format:
        name: classify_presentation
        version: "1.0"
        description: "..."
        model: claude-sonnet-4-20250514
        max_tokens: 8192
        variables:
          - name: filenames
            description: "..."
        template: |
          Your prompt text with {variable} placeholders.

    Usage:
        pt = PromptTemplate.load("classify_presentation")
        text = pt.render(n=261, filenames="...")
    """

    def __init__(self, data: dict):
        self.name: str        = data["name"]
        self.version: str     = str(data["version"])
        self.description: str = data.get("description", "")
        self.model: str       = data.get("model", "claude-sonnet-4-20250514")
        self.max_tokens: int  = int(data.get("max_tokens", 4096))
        self.template: str    = data["template"]
        self.variables: list  = data.get("variables", [])

    @classmethod
    def load(cls, name: str) -> "PromptTemplate":
        """Load prompt by name from PROMPTS_DIR/{name}.yaml"""
        path = PROMPTS_DIR / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data)

    def render(self, **kwargs: Any) -> str:
        """Render template with provided variables."""
        try:
            return self.template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"Missing variable {e} for prompt '{self.name}'") from e

    def __repr__(self) -> str:
        return f"<PromptTemplate name={self.name!r} version={self.version!r}>"


class PromptLogger:
    """
    Appends one JSON line per AI call to logs/prompt_history.jsonl.

    Each log entry:
        timestamp       : ISO-8601 UTC
        prompt_name     : name from YAML
        prompt_version  : version from YAML
        model           : model ID used
        provider        : "sonnet" | "gemini" | "regex" | ...
        batch_num       : 1-based batch index
        batch_size      : number of items in this batch
        input_tokens    : estimated (chars / 4)
        output_tokens   : estimated
        input_preview   : first 300 chars of rendered prompt
        output_preview  : first 300 chars of raw response
        error           : null or error message
    """

    def __init__(self, log_path: Path = DEFAULT_LOG_PATH):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        prompt_name: str,
        prompt_version: str,
        model: str,
        provider: str,
        rendered_prompt: str,
        raw_output: str,
        batch_num: int = 1,
        batch_size: int = 0,
        error: str | None = None,
    ) -> None:
        entry = {
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "prompt_name":    prompt_name,
            "prompt_version": prompt_version,
            "model":          model,
            "provider":       provider,
            "batch_num":      batch_num,
            "batch_size":     batch_size,
            "input_tokens":   len(rendered_prompt) // 4,
            "output_tokens":  len(raw_output) // 4,
            "input_preview":  rendered_prompt[:300].replace("\n", " "),
            "output_preview": raw_output[:300].replace("\n", " "),
            "error":          error,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.debug(
            "prompt logged: %s v%s  batch=%d/%d  in~%d  out~%d tokens",
            prompt_name, prompt_version, batch_num, batch_size,
            entry["input_tokens"], entry["output_tokens"],
        )
