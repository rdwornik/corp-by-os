"""Validates generated manifest against CKE expected format."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CKE_REQUIRED_TOP_LEVEL = {"schema_version", "output_dir", "files"}
_CKE_REQUIRED_ENTRY = {"id", "path"}
_V21_FIELDS = {"content_origin", "source_category", "source_locator", "routing_confidence"}
_VALID_DOC_TYPES = {
    "video",
    "audio",
    "document",
    "presentation",
    "slides",
    "spreadsheet",
    "note",
    "transcript",
}


class ManifestValidationError(Exception):
    """Raised when manifest fails contract validation."""


def validate_manifest(
    manifest_path: Path,
    check_files_exist: bool = True,
) -> bool:
    """Validate that a generated manifest is CKE-compatible.

    Checks:
    1. File parses as valid JSON
    2. schema_version == 1
    3. All required CKE top-level fields present
    4. All required CKE entry fields present per file
    5. v2.1 provenance fields present and valid types
    6. source_locator uses forward slashes (no backslashes)
    7. routing_confidence between 0.0 and 1.0
    8. All file paths exist and are readable (optional)
    9. doc_type is a known value

    Returns True if valid, raises ManifestValidationError with details if not.
    """
    errors: list[str] = []

    # 1. Parse JSON
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"Invalid JSON: {exc}") from exc
    except FileNotFoundError as exc:
        raise ManifestValidationError(f"Manifest file not found: {manifest_path}") from exc

    # 2. Schema version
    if data.get("schema_version") != 1:
        errors.append(f"schema_version must be 1, got {data.get('schema_version')!r}")

    # 3. Top-level fields
    missing_top = _CKE_REQUIRED_TOP_LEVEL - set(data.keys())
    if missing_top:
        errors.append(f"Missing top-level fields: {missing_top}")

    # 4-9. File entries
    files = data.get("files", [])
    if not isinstance(files, list):
        errors.append("'files' must be a list")
        files = []

    seen_ids: set[str] = set()
    for i, entry in enumerate(files):
        prefix = f"files[{i}]"

        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be a dict")
            continue

        # Required CKE fields
        for field_name in _CKE_REQUIRED_ENTRY:
            if field_name not in entry:
                errors.append(f"{prefix}: missing required field '{field_name}'")

        # Unique ID check
        entry_id = entry.get("id", "")
        if entry_id in seen_ids:
            errors.append(f"{prefix}: duplicate id '{entry_id}'")
        seen_ids.add(entry_id)

        # doc_type validation
        doc_type = entry.get("doc_type", "document")
        if doc_type not in _VALID_DOC_TYPES:
            errors.append(f"{prefix}: unknown doc_type '{doc_type}'")

        # v2.1 fields
        missing_v21 = _V21_FIELDS - set(entry.keys())
        if missing_v21:
            errors.append(f"{prefix}: missing v2.1 fields: {missing_v21}")

        # source_locator: no backslashes
        locator = entry.get("source_locator", "")
        if "\\" in str(locator):
            errors.append(f"{prefix}: source_locator contains backslashes: '{locator}'")

        # routing_confidence: 0.0-1.0
        confidence = entry.get("routing_confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
                errors.append(f"{prefix}: routing_confidence must be 0.0-1.0, got {confidence!r}")

        # File existence check
        if check_files_exist:
            file_path = entry.get("path", "")
            if file_path and not Path(file_path).exists():
                errors.append(f"{prefix}: file not found: '{file_path}'")

    if errors:
        detail = "\n  - ".join(errors)
        raise ManifestValidationError(
            f"Manifest validation failed ({len(errors)} errors):\n  - {detail}"
        )

    log.info("Manifest validation passed: %d files", len(files))
    return True
