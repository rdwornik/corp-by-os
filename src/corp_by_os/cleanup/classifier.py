"""Gemini-powered file classifier for cleanup.

Classifies files by name, extension, size, and location to determine
where they should be moved. Returns structured JSON proposals.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .scanner import FileInfo

log = logging.getLogger(__name__)

CLASSIFY_PROMPT = """\
You are classifying files in a pre-sales engineer's work folder.
Given a filename, extension, size, and current location, determine:
1. Where this file should go (target folder)
2. What it should be named (following conventions)
3. Confidence level (0.0-1.0)

Available target folders:
- 10_Projects/{Client}_{Product}/ -- client opportunity workspaces
- 20_Extra_Initiatives/{descriptive_name}/ -- internal non-client projects
- 30_Templates/01_Presentation_Decks/ -- reusable presentation templates
- 30_Templates/02_Demo_Scripts/ -- demo scripts and payloads
- 30_Templates/03_Discovery_Tools/ -- discovery frameworks
- 30_Templates/90_Reference_Baselines/ -- brand guidelines, service descriptions
- 50_RFP/ -- RFP responses
- 50_RFP/_databases/ -- master RFP databases (xlsx, csv)
- 60_Source_Library/01_Product_Docs/ -- product documentation
- 60_Source_Library/02_Training_Enablement/ -- training materials
- 60_Source_Library/03_Competitive/ -- competitive intel
- 70_Admin/ -- administrative files, bookmarks, logs
- DELETE -- file has no value (empty, duplicate, junk)
- KEEP -- file is already in the right place

File naming conventions:
- Projects: {Client}_{Date}_{Topic}.{ext} or descriptive
- Templates: descriptive, no client name
- RFP databases: RFP_Database_{Product}.xlsx

Classification hints by extension:
- .url -> almost always 70_Admin/ (bookmarks)
- .log -> almost always 70_Admin/ or DELETE
- .pptx -> usually 30_Templates/ or 20_Extra_Initiatives/ unless client-specific
- .xlsx with "RFP_Database" in name -> 50_RFP/_databases/
- Meeting notes -> 10_Projects/ if client identified, else 20_Extra_Initiatives/
- RFP responses -> 50_RFP/

Respond ONLY with a JSON object (no markdown, no explanation):
{"action": "move|delete|keep", "destination_folder": "path", \
"proposed_name": "filename", "reason": "brief explanation", "confidence": 0.85}
"""


@dataclass
class Classification:
    """Result of classifying a single file."""

    file_info: FileInfo
    action: str
    destination_folder: str
    proposed_name: str
    reason: str
    confidence: float


def _build_user_message(info: FileInfo) -> str:
    """Build the user message for Gemini classification."""
    size_kb = info.size_bytes / 1024
    return (
        f"File: {info.name}\n"
        f"Extension: {info.extension}\n"
        f"Size: {size_kb:.1f} KB\n"
        f"Current location: {info.relative_path}\n"
    )


def _parse_response(text: str) -> dict[str, Any]:
    """Parse Gemini JSON response, handling markdown fences."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Strip first and last lines (fences)
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        cleaned = inner.strip()
    return json.loads(cleaned)


def classify_file(
    file_info: FileInfo,
    client: Any | None = None,
    model: str = "gemini-2.5-flash",
) -> Classification:
    """Classify a single file using Gemini.

    If client is None, attempts to create one from GEMINI_API_KEY env var.
    """
    if client is None:
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        client = genai.Client(api_key=api_key)

    from google import genai as genai_module

    user_msg = _build_user_message(file_info)

    try:
        response = client.models.generate_content(
            model=model,
            contents=user_msg,
            config=genai_module.types.GenerateContentConfig(
                system_instruction=CLASSIFY_PROMPT,
                temperature=0.1,
                max_output_tokens=300,
            ),
        )
        parsed = _parse_response(response.text)
    except Exception as exc:
        log.warning("Classification failed for %s: %s", file_info.name, exc)
        return Classification(
            file_info=file_info,
            action="keep",
            destination_folder=file_info.current_folder,
            proposed_name=file_info.name,
            reason=f"Classification error: {exc}",
            confidence=0.0,
        )

    return Classification(
        file_info=file_info,
        action=parsed.get("action", "keep"),
        destination_folder=parsed.get("destination_folder", file_info.current_folder),
        proposed_name=parsed.get("proposed_name", file_info.name),
        reason=parsed.get("reason", ""),
        confidence=float(parsed.get("confidence", 0.0)),
    )


def classify_batch(
    files: list[FileInfo],
    model: str = "gemini-2.5-flash",
) -> list[Classification]:
    """Classify multiple files sequentially.

    Uses a single Gemini client for all calls.
    """
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)
    results: list[Classification] = []

    for i, info in enumerate(files, 1):
        log.info("Classifying [%d/%d]: %s", i, len(files), info.name)
        result = classify_file(info, client=client, model=model)
        results.append(result)

    return results
