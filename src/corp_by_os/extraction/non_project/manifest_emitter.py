"""Builds CKE-compatible manifest with v2.1 provenance fields.

CKE Manifest Schema (from corp-knowledge-extractor/src/manifest.py):

    Format: JSON
    Schema version: 1

    Top-level fields:
        schema_version  int       (required, must be 1)
        project         str       (optional, default "unknown")
        output_dir      str       (required, path for output packages)
        config          dict      (optional, custom config)
        files           list      (required, list of file entries)

    File entry fields:
        id              str       (required, unique identifier)
        path            str       (required, absolute file path)
        doc_type        str       (optional, default "document")
                                  values: video, audio, document, presentation,
                                          slides, spreadsheet, note, transcript
        name            str       (optional, defaults to id)
        client          str|null  (optional)
        project         str|null  (optional, overrides manifest.project)

    Extra fields in file entries are ignored by CKE but preserved
    in the manifest file. We add v2.1 provenance fields here for
    downstream consumers (corp-os-meta, corp-by-os post-processing).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .folder_policy import ExtractionPolicy
from .routing import RouteInfo
from .scanner import ScanResult

log = logging.getLogger(__name__)

# Map file extensions to CKE doc_type values
_EXT_TO_DOC_TYPE: dict[str, str] = {
    ".pptx": "presentation",
    ".ppt": "presentation",
    ".pdf": "document",
    ".docx": "document",
    ".doc": "document",
    ".txt": "document",
    ".md": "note",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".xlsm": "spreadsheet",
    ".csv": "spreadsheet",
    ".json": "document",
    ".msg": "document",
    ".eml": "document",
    ".mp4": "video",
    ".mkv": "video",
    ".avi": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".py": "document",
}


def _make_entry_id(relative_path: str) -> str:
    """Generate a stable, filesystem-safe ID from relative path.

    Examples:
        "01_Presentation_Decks/Platform Overview.pptx"
        -> "01-presentation-decks--platform-overview"
    """
    # Include extension (without dot) to distinguish same-name files
    parts = relative_path.rsplit(".", 1)
    stem = parts[0]
    ext = parts[1] if len(parts) > 1 else ""
    # Replace path separators and spaces
    slug = stem.lower().replace("/", "--").replace("\\", "--").replace(" ", "-")
    if ext:
        slug = f"{slug}--{ext.lower()}"
    # Remove non-alphanumeric except hyphens and underscores
    slug = re.sub(r"[^a-z0-9\-_]", "", slug)
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "--", slug)
    return slug.strip("-")


def _resolve_doc_type(extension: str) -> str:
    """Map file extension to CKE doc_type string."""
    return _EXT_TO_DOC_TYPE.get(extension.lower(), "document")


def build_manifest(
    scan_results: list[ScanResult],
    route_info: RouteInfo,
    policy: ExtractionPolicy,
    output_dir: Path,
    project_name: str | None = None,
    mywork_root: Path | None = None,
) -> dict[str, Any]:
    """Build a CKE-compatible manifest dict.

    Returns the manifest as a dict (caller decides whether to write it).
    """
    files: list[dict[str, Any]] = []

    for scan in scan_results:
        entry_id = _make_entry_id(scan.relative_path)

        # source_locator: path relative to MyWork root with forward slashes
        if mywork_root:
            try:
                source_locator = str(scan.absolute_path.relative_to(mywork_root.resolve())).replace(
                    "\\", "/"
                )
            except ValueError:
                source_locator = scan.relative_path
        else:
            source_locator = scan.relative_path

        entry: dict[str, Any] = {
            "id": entry_id,
            "path": str(scan.absolute_path),
            "doc_type": _resolve_doc_type(scan.extension),
            "name": scan.absolute_path.stem,
            # v2.1 provenance fields (ignored by CKE, used by corp-by-os)
            "content_origin": route_info.content_origin,
            "source_category": route_info.source_category,
            "source_locator": source_locator,
            "routing_confidence": route_info.routing_confidence,
        }
        files.append(entry)

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "project": project_name or route_info.source_category,
        "output_dir": str(output_dir),
        "config": {
            "provenance_scope": route_info.provenance_scope,
            "vault_target": route_info.vault_target,
            "privacy": policy.privacy,
            "credential_scrubbing": policy.credential_scrubbing,
        },
        "files": files,
    }

    return manifest


def write_manifest(manifest: dict[str, Any], manifest_path: Path) -> Path:
    """Write manifest dict to a JSON file."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    log.info("Wrote manifest with %d files to %s", len(manifest["files"]), manifest_path)
    return manifest_path
