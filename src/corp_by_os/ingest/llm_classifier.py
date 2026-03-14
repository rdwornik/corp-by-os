"""LLM fallback classifier for files that heuristic routing cannot match.

Uses Gemini Flash with schema-constrained output to classify quarantined
files. Results are ALWAYS staged (never directly routed) because LLM
confidence is inherently lower than heuristic matching.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from google import genai  # type: ignore[import-untyped]
except ImportError:
    genai = None  # type: ignore[assignment]

CLASSIFY_PROMPT = """\
You are classifying a file for a pre-sales engineer's knowledge management system.

The engineer works at Blue Yonder, selling enterprise supply chain software \
(WMS, TMS, Planning, Platform) across EMEA.

Given this file information:
- Filename: {filename}
- Extension: {extension}
- Size: {size_mb} MB
- Current location: {current_folder}
- Parent folder: {parent_folder}

Available destinations:
{destinations}

Classify this file. Respond with ONLY this JSON:
{{
  "destination": "path/to/destination",
  "series_id": null,
  "topics": ["topic1", "topic2"],
  "source_category": "training|product_doc|competitive|rfp|meeting|demo|template",
  "confidence": 0.0,
  "reasoning": "Brief explanation"
}}

If you cannot confidently classify (confidence < 0.5), \
set destination to "00_Inbox/_Unmatched".
"""


@dataclass
class LLMClassification:
    """Result of LLM-based file classification."""

    destination: str
    series_id: str | None
    topics: list[str]
    source_category: str
    confidence: float
    reasoning: str


def _parse_llm_json(raw_text: str) -> dict | None:
    """Extract and parse JSON from an LLM response.

    Handles responses wrapped in markdown code fences or with
    surrounding prose.
    """
    # Try direct parse first
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code fence
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw_text)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } block
    brace_match = re.search(r"\{[\s\S]*\}", raw_text)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _no_match_classification(reason: str) -> LLMClassification:
    """Return a zero-confidence classification for error cases."""
    return LLMClassification(
        destination="00_Inbox/_Unmatched",
        series_id=None,
        topics=[],
        source_category="unknown",
        confidence=0.0,
        reasoning=reason,
    )


def classify_file_llm(
    filename: str,
    extension: str,
    size_mb: float,
    current_folder: str,
    parent_folder: str | None,
    registry_destinations: list[str],
    model: str = "gemini-2.0-flash",
) -> LLMClassification:
    """Classify a single file using Gemini Flash.

    Returns LLMClassification with destination and metadata.
    All LLM-classified files should go to _Staging/ regardless
    of the confidence — the caller handles that.
    """
    if genai is None:
        logger.warning("google-genai not installed — LLM classification unavailable")
        return _no_match_classification("google-genai SDK not installed")

    destinations_str = "\n".join(f"- {d}" for d in registry_destinations)

    prompt = CLASSIFY_PROMPT.format(
        filename=filename,
        extension=extension,
        size_mb=f"{size_mb:.2f}",
        current_folder=current_folder,
        parent_folder=parent_folder or "none",
        destinations=destinations_str,
    )

    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=model,
            contents=prompt,
        )
        raw_text = response.text
    except Exception as exc:
        logger.warning("LLM API call failed for %s: %s", filename, exc)
        return _no_match_classification(f"LLM API error: {exc}")

    parsed = _parse_llm_json(raw_text)

    if not parsed:
        logger.warning("LLM classification failed to parse for %s", filename)
        return _no_match_classification("LLM response could not be parsed")

    return LLMClassification(
        destination=parsed.get("destination", "00_Inbox/_Unmatched"),
        series_id=parsed.get("series_id"),
        topics=parsed.get("topics", []),
        source_category=parsed.get("source_category", "unknown"),
        confidence=min(parsed.get("confidence", 0.5), 0.85),
        reasoning=parsed.get("reasoning", ""),
    )


def classify_quarantined_batch(
    ops: "OpsDB",  # noqa: F821
    registry: "ContentRegistry",  # noqa: F821
    mywork_root: Path,
    model: str = "gemini-2.0-flash",
    budget: float = 0.50,
    dry_run: bool = False,
) -> list[dict]:
    """Classify all quarantined files using LLM.

    Reads quarantined assets from ops.db, classifies each,
    moves to _Staging/ at the classified destination.

    Budget cap: stops when estimated cost exceeds budget.

    Returns list of classification results.
    """
    quarantined = ops.get_assets_by_status("quarantined")
    if not quarantined:
        logger.info("No quarantined files to classify.")
        return []

    destinations = _get_all_destinations(registry)

    results: list[dict] = []
    estimated_cost = 0.0
    cost_per_call = 0.001  # rough estimate for Flash

    for asset in quarantined:
        if estimated_cost + cost_per_call > budget:
            logger.warning(
                "Budget cap reached ($%.2f). %d files remaining.",
                budget, len(quarantined) - len(results),
            )
            break

        classification = classify_file_llm(
            filename=asset["filename"],
            extension=asset["extension"],
            size_mb=asset["size_bytes"] / 1024 / 1024,
            current_folder=asset["folder_l1"],
            parent_folder=asset.get("folder_l2"),
            registry_destinations=destinations,
            model=model,
        )
        estimated_cost += cost_per_call

        results.append({
            "asset_id": asset["id"],
            "path": asset["path"],
            "filename": asset["filename"],
            "classification": classification,
        })

        if not dry_run and classification.destination != "00_Inbox/_Unmatched":
            _move_to_staging(
                asset, classification, mywork_root, ops, cost_per_call,
            )

    return results


def _move_to_staging(
    asset: dict,
    classification: LLMClassification,
    mywork_root: Path,
    ops: "OpsDB",  # noqa: F821
    cost: float,
) -> None:
    """Move a classified file from quarantine to _Staging at its destination."""
    src_path = mywork_root / asset["path"].replace("/", "\\")
    if not src_path.exists():
        logger.warning("Source file not found for staging: %s", asset["path"])
        return

    staging_dest = (
        mywork_root
        / classification.destination.replace("/", "\\")
        / "_Staging"
    )
    staging_dest.mkdir(parents=True, exist_ok=True)
    dest_file = staging_dest / src_path.name

    try:
        shutil.move(str(src_path), str(dest_file))
    except OSError as exc:
        logger.error("Failed to stage %s: %s", asset["filename"], exc)
        return

    dest_rel = str(
        dest_file.relative_to(mywork_root.resolve())
    ).replace("\\", "/")

    ops.update_asset_path(asset["path"], dest_rel)
    ops.update_asset_status(
        dest_rel,
        "staged",
        routed_to=dest_rel,
        routed_method="llm",
        routed_confidence=classification.confidence,
        reasoning=classification.reasoning,
        cost=cost,
    )
    ops.log_event(
        action="llm_classified",
        asset_id=asset["id"],
        source_path=asset["path"],
        destination_path=dest_rel,
        method="llm",
        confidence=classification.confidence,
        reasoning=classification.reasoning,
        cost=cost,
    )


def _get_all_destinations(registry: "ContentRegistry") -> list[str]:  # noqa: F821
    """Extract all valid destinations from registry."""
    dests: set[str] = set()
    for series in registry.data.get("series", {}).values():
        if series.get("destination"):
            dests.add(series["destination"])
    for rule in registry.data.get("destination_rules", []):
        if rule.get("destination"):
            dests.add(rule["destination"])
    # Standard folders
    dests.update([
        "10_Projects",
        "20_Extra_Initiatives",
        "30_Templates/01_Presentation_Decks",
        "30_Templates/02_Demo_Scripts",
        "30_Templates/03_Discovery_Tools",
        "30_Templates/90_Reference_Baselines",
        "50_RFP",
        "50_RFP/_databases",
        "60_Source_Library/01_Product_Docs",
        "60_Source_Library/02_Training_Enablement",
        "60_Source_Library/03_Competitive",
        "70_Admin",
    ])
    return sorted(dests)
