"""Classify files using Tier 1 local metadata — no LLM needed.

Rename strategy: IMPROVE current filename, never replace it with slide title.
Move strategy: Only move files from 00_Inbox. Project files NEVER move out.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Folders whose files must NEVER be moved elsewhere
_PINNED_FOLDERS: set[str] = {
    "10_Projects",
    "20_Extra_Initiatives",
}

# Folders whose files MAY be moved (inbox = misplaced files)
_MOVABLE_FOLDERS: set[str] = {
    "00_Inbox",
}

# Generic filenames that should be enriched with client/context
_GENERIC_NAMES: set[str] = {
    "document",
    "document1",
    "presentation",
    "presentation1",
    "untitled",
    "new",
    "draft",
    "file",
    "test",
    "temp",
    "book1",
    "sheet1",
    "workbook1",
}

# Prefixes/suffixes that indicate a copy or duplicate
_COPY_PATTERN = re.compile(
    r"^Copy\s+of\s+|"  # "Copy of Budget.xlsx"
    r"\s*-\s*Copy$|"  # "Budget - Copy.xlsx"
    r"\s*\(\d+\)$|"  # "Budget (1).xlsx"
    r"\s*-\s*\d+$",  # "Budget - 2.xlsx"  (but not "v2")
    re.IGNORECASE,
)

# Characters that need cleanup (spaces, special path chars)
_NEEDS_SPACE_CLEANUP = re.compile(r"[ ]")

# Map extracted content type keywords to routing folders
_TYPE_TO_FOLDER: dict[str, str] = {
    "training": "60_Source_Library/02_Training_Enablement",
    "enablement": "60_Source_Library/02_Training_Enablement",
    "product_docs": "60_Source_Library/01_Product_Docs",
    "documentation": "60_Source_Library/01_Product_Docs",
    "industry": "60_Source_Library/03_Industry_Knowledge",
    "demo": "30_Templates/02_Demo_Scripts",
    "demo_script": "30_Templates/02_Demo_Scripts",
    "questionnaire": "30_Templates/03_Discovery_Tools",
    "discovery": "30_Templates/03_Discovery_Tools",
    "template": "30_Templates/01_Presentation_Decks",
    "rfp": "50_RFP",
}


@dataclass
class ClassificationResult:
    """File classification from local metadata."""

    current_path: str
    proposed_name: str | None = None
    proposed_folder: str | None = None
    confidence: float = 0.0
    reasoning: str = ""


def classify_from_metadata(
    scan_result: dict,
    routing_map: dict,
) -> ClassificationResult:
    """Classify a single file using Tier 1 metadata.

    Rename logic: improve current filename, never replace with slide title.
    Move logic: only files in 00_Inbox. Project files NEVER move.
    """
    path_str = scan_result.get("path", "")
    meta = scan_result.get("metadata", {})
    text_preview = (meta.get("text_preview", "") or "")[:500]

    result = ClassificationResult(current_path=path_str)
    current_name = Path(path_str).name
    stem = Path(current_name).stem
    ext = Path(current_name).suffix
    top_folder = _extract_top_folder(path_str)

    # --- Proposed filename (improve, don't replace) ---
    rename_action, proposed, conf = _propose_rename(
        current_name,
        stem,
        ext,
        meta,
        path_str,
    )

    if rename_action != "skip":
        result.proposed_name = proposed
        result.confidence = conf
        result.reasoning = rename_action
    else:
        # No rename needed — confidence is for the "no action" decision
        result.confidence = 0.0

    # --- Proposed folder (only for movable folders) ---
    if top_folder in _PINNED_FOLDERS:
        # HARD RULE: files in project/initiative folders NEVER move
        pass
    elif top_folder in _MOVABLE_FOLDERS:
        folder = _determine_folder(meta, text_preview, routing_map)
        if folder and not path_str.startswith(folder):
            result.proposed_folder = folder
            # Move confidence is lower — needs more review
            result.confidence = min(result.confidence, 0.70) if result.confidence else 0.70
            result.reasoning += f", move→{folder}"

    return result


def classify_batch(
    scan_results: list[dict],
    routing_map: dict,
) -> list[ClassificationResult]:
    """Classify a batch of files. Only returns results that need action."""
    results = []
    for sr in scan_results:
        r = classify_from_metadata(sr, routing_map)
        # Only include files that actually need action
        if r.proposed_name or r.proposed_folder:
            results.append(r)
    return results


def _propose_rename(
    current_name: str,
    stem: str,
    ext: str,
    meta: dict,
    path_str: str,
) -> tuple[str, str | None, float]:
    """Determine rename action for a file.

    Returns: (action_type, proposed_name_or_None, confidence)
    Actions: "skip", "space_cleanup", "remove_copy", "enrich_generic"
    """
    # Check for "Copy of" / "(1)" patterns
    copy_match = _COPY_PATTERN.search(stem)
    if copy_match:
        cleaned = _COPY_PATTERN.sub("", stem).strip()
        if cleaned:
            cleaned = _clean_spaces(cleaned)
            return "remove_copy", f"{cleaned}{ext}", 0.92

    # Check if name is generic (needs enrichment)
    stem_lower = stem.lower().strip()
    if stem_lower in _GENERIC_NAMES:
        enriched = _enrich_generic_name(stem, ext, meta, path_str)
        if enriched:
            return "enrich_generic", enriched, 0.80
        # Can't enrich (no context) — skip
        return "skip", None, 0.0

    # Check if spaces need cleanup
    if _NEEDS_SPACE_CLEANUP.search(current_name):
        cleaned = _clean_spaces(stem)
        if cleaned != stem:
            return "space_cleanup", f"{cleaned}{ext}", 0.95

    # Name is already clean — no action needed
    return "skip", None, 0.0


def _clean_spaces(stem: str) -> str:
    """Replace spaces with underscores, preserving hyphens and structure."""
    return re.sub(r"\s+", "_", stem)


def _enrich_generic_name(
    stem: str,
    ext: str,
    meta: dict,
    path_str: str,
) -> str | None:
    """Enrich a generic filename with context from path and metadata.

    E.g. "Presentation.pptx" in Lenzing_Planning/ → "Lenzing_Planning_Presentation.pptx"
    """
    # Try to extract client/project from folder structure
    parts = Path(path_str).parts
    context_parts: list[str] = []

    for part in parts:
        # Skip top-level numbered folders and the file itself
        if re.match(r"\d{2}_", part) or part == Path(path_str).name:
            continue
        # Use subfolder names as context (e.g. "Lenzing_Planning")
        if len(part) > 2 and part not in {"_knowledge", "source", "docs"}:
            context_parts.append(_clean_spaces(part))
            if len(context_parts) >= 2:
                break

    if context_parts:
        prefix = "_".join(context_parts)
        clean_stem = _clean_spaces(stem)
        return f"{prefix}_{clean_stem}{ext}"

    return None


def generate_filename(
    metadata: dict,
    client: str | None = None,
    extension: str = "",
) -> str:
    """Generate proper filename from extracted metadata.

    Used for enriching generic names, NOT for replacing good names.
    Pattern: {Client_}{Title_Slug}.{ext}. Max 80 chars for the stem.
    """
    title = metadata.get("title", "") or ""
    title = title.strip()

    if not title:
        return ""

    slug = _slugify(title)

    if client:
        client_slug = _slugify(client)
        slug = f"{client_slug}_{slug}"

    if len(slug) > 80:
        slug = slug[:80].rstrip("_")

    if extension and not extension.startswith("."):
        extension = f".{extension}"

    return f"{slug}{extension}"


def _slugify(text: str) -> str:
    """Convert text to filesystem-safe slug.

    Replaces spaces/special chars with underscores, collapses runs,
    strips leading/trailing underscores.
    """
    slug = re.sub(r"[\s\-–—/\\:;,]+", "_", text)
    slug = re.sub(r"[^a-zA-Z0-9_.]", "", slug)
    slug = re.sub(r"_{2,}", "_", slug)
    return slug.strip("_")


def _determine_folder(
    meta: dict,
    text_preview: str,
    routing_map: dict,
) -> str | None:
    """Determine target folder from metadata type and text signals.

    Only used for files in movable folders (00_Inbox).
    """
    doc_type = (meta.get("type", "") or "").lower()
    if doc_type in _TYPE_TO_FOLDER:
        return _TYPE_TO_FOLDER[doc_type]

    headings = meta.get("headings", [])
    heading_text = " ".join(headings).lower() if headings else ""
    combined = f"{heading_text} {text_preview}".lower()

    if any(kw in combined for kw in ("rfp", "request for proposal", "response template")):
        return "50_RFP"
    if any(kw in combined for kw in ("training", "hands-on", "exercise", "lab", "workshop")):
        return "60_Source_Library/02_Training_Enablement"
    if any(kw in combined for kw in ("demo script", "demo scenario", "click path")):
        return "30_Templates/02_Demo_Scripts"
    if any(kw in combined for kw in ("discovery", "questionnaire", "assessment")):
        return "30_Templates/03_Discovery_Tools"

    return None


def _extract_top_folder(path: str) -> str | None:
    """Extract the MyWork top-level folder from a path."""
    parts = Path(path).parts
    for part in parts:
        if re.match(r"\d{2}_", part):
            return part
    return None
