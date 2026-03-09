"""Gemini Flash LLM for intent classification.

Called ONLY when keyword matching fails.
Returns structured JSON parsed into Intent.

Uses google.genai (new SDK), NOT google.generativeai.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from corp_by_os.intent_router import Intent
from corp_by_os.models import Workflow

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a workflow router for a pre-sales engineer's productivity system.
Given the user's message, determine which workflow to execute and extract parameters.

Available workflows:
{workflows_summary}

Known projects: {project_list}
Known products: Planning, WMS, TMS, CatMan, Network, E2E, Flexis, Migration, Retail

The user speaks Polish or English. Respond in the same language.

Return ONLY valid JSON:
{{
  "workflow_id": "workflow_id_or_null",
  "parameters": {{
    "client": "string or null",
    "product": "string or null",
    "project": "string or null",
    "topic": "string or null",
    "date": "YYYY-MM-DD or null",
    "priority": "high|medium|low or null",
    "reason": "string or null",
    "title": "string or null",
    "notes": "string or null"
  }},
  "confidence": 0.0-1.0,
  "response_text": "Human-friendly response if chitchat or clarification needed"
}}

If the message is chitchat or unclear, set workflow_id to null and provide response_text.\
"""


def _get_usage_path() -> Path:
    """Get the path to the usage tracking file."""
    app_data = os.environ.get(
        "APP_DATA_PATH",
        os.path.expandvars(r"%LOCALAPPDATA%\corp-by-os"),
    )
    return Path(app_data) / "usage.json"


def _check_daily_cap() -> bool:
    """Check if we're under the daily LLM call cap.

    Returns True if we can make a call, False if cap reached.
    """
    cap = int(os.environ.get("LLM_DAILY_CAP", "30"))
    usage_path = _get_usage_path()

    today_str = date.today().isoformat()
    usage = _load_usage(usage_path)

    if usage.get("date") != today_str:
        # New day, reset counter
        usage = {"date": today_str, "calls": 0}

    return usage.get("calls", 0) < cap


def _increment_usage() -> None:
    """Increment the daily usage counter."""
    usage_path = _get_usage_path()
    today_str = date.today().isoformat()
    usage = _load_usage(usage_path)

    if usage.get("date") != today_str:
        usage = {"date": today_str, "calls": 0}

    usage["calls"] = usage.get("calls", 0) + 1
    _save_usage(usage_path, usage)


def _load_usage(path: Path) -> dict:
    """Load usage data from JSON file."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_usage(path: Path, data: dict) -> None:
    """Save usage data to JSON file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to save usage data: %s", e)


def _build_workflows_summary(workflows: dict[str, Workflow]) -> str:
    """Build a summary of available workflows for the LLM prompt."""
    lines = []
    for wf_id, wf in workflows.items():
        params_str = ", ".join(
            f"{name} ({'required' if p.required else 'optional'})"
            for name, p in wf.parameters.items()
        )
        lines.append(f"- {wf_id}: {wf.description} | params: {params_str}")
    return "\n".join(lines)


def _build_project_list() -> str:
    """Build a comma-separated list of known projects."""
    try:
        from corp_by_os.project_resolver import list_all_project_ids
        projects = list_all_project_ids()
        return ", ".join(projects[:30])  # limit to avoid token bloat
    except Exception:
        return "(unavailable)"


def classify_intent(
    user_input: str,
    workflows: dict[str, Workflow],
    context: dict | None = None,
) -> Intent:
    """Classify user intent via Gemini Flash.

    Args:
        user_input: Raw user message.
        workflows: Available workflow definitions.
        context: Optional conversation history.

    Returns:
        Intent parsed from LLM response.
    """
    if not _check_daily_cap():
        return Intent(
            source="none",
            response_text="Limit LLM na dziś wyczerpany. Użyj `corp run <workflow>` bezpośrednio.",
        )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return Intent(
            source="none",
            response_text="Brak GEMINI_API_KEY. Ustaw w .env lub użyj `corp chat --no-llm`.",
        )

    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # Build prompt
    system_prompt = _SYSTEM_PROMPT.format(
        workflows_summary=_build_workflows_summary(workflows),
        project_list=_build_project_list(),
    )

    # Build messages with context
    messages = []
    if context and "history" in context:
        for turn in context["history"][-5:]:  # last 5 turns
            messages.append(f"User: {turn.get('user', '')}")
            if turn.get("response"):
                messages.append(f"Assistant: {turn['response']}")

    messages.append(f"User: {user_input}")
    user_message = "\n".join(messages)

    try:
        from google import genai

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=user_message,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
                max_output_tokens=500,
            ),
        )

        _increment_usage()

        return _parse_llm_response(response.text)

    except ImportError:
        logger.warning("google-genai not installed. pip install google-genai")
        return Intent(
            source="none",
            response_text="google-genai nie zainstalowane. `pip install google-genai`",
        )
    except Exception as e:
        logger.warning("Gemini API call failed: %s", e)
        return Intent(
            source="none",
            response_text=f"Błąd LLM: {e}. Użyj `corp run <workflow>` bezpośrednio.",
        )


def _parse_llm_response(text: str) -> Intent:
    """Parse structured JSON from LLM response into Intent."""
    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to extract JSON from within the text
        json_match = _extract_json(cleaned)
        if json_match:
            data = json.loads(json_match)
        else:
            logger.warning("Failed to parse LLM JSON response: %s", text[:200])
            return Intent(
                source="llm",
                confidence=0.0,
                response_text=text[:300],
            )

    # Build params dict, filtering out null values
    raw_params = data.get("parameters", {})
    params = {k: v for k, v in raw_params.items() if v is not None}

    return Intent(
        workflow_id=data.get("workflow_id"),
        parameters=params,
        confidence=float(data.get("confidence", 0.5)),
        source="llm",
        response_text=data.get("response_text"),
    )


def _extract_json(text: str) -> str | None:
    """Try to extract a JSON object from text."""
    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    return None
