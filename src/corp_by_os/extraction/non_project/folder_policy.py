"""Reads folder_manifest.yaml and returns extraction policy."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

MANIFEST_FILENAME = "folder_manifest.yaml"


@dataclass
class ExtractionPolicy:
    """What and how to extract from a folder."""

    enabled: bool
    scope: str
    extract_on_change: bool = False
    settle_minutes: int = 30
    allow_extensions: list[str] = field(default_factory=list)
    privacy: str = "internal"
    credential_scrubbing: bool = False


class PolicyError(Exception):
    """Raised when folder_manifest.yaml is missing or invalid."""


def _load_manifest_yaml(folder_path: Path) -> dict | None:
    """Load folder_manifest.yaml from a directory, or None if absent."""
    manifest_file = folder_path / MANIFEST_FILENAME
    if not manifest_file.exists():
        return None
    with open(manifest_file, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_policy(folder_path: Path) -> ExtractionPolicy:
    """Load extraction policy from folder_manifest.yaml.

    Walks up from folder_path to find the nearest manifest.
    If the folder itself has a manifest, its fields override the parent.
    """
    folder_path = folder_path.resolve()

    # Try loading manifest from the folder itself
    own_manifest = _load_manifest_yaml(folder_path)

    # Try loading from parent for subfolder merge
    parent_manifest = _load_manifest_yaml(folder_path.parent)

    # Determine base manifest
    if own_manifest is not None:
        base = own_manifest
    elif parent_manifest is not None:
        base = parent_manifest
    else:
        raise PolicyError(f"No {MANIFEST_FILENAME} found in {folder_path} or its parent")

    extraction = base.get("extraction", {})
    enabled = extraction.get("enabled", False)
    scope = extraction.get("scope", "")

    policy = ExtractionPolicy(
        enabled=enabled,
        scope=scope,
        extract_on_change=extraction.get("extract_on_change", False),
        settle_minutes=extraction.get("settle_minutes", 30),
        allow_extensions=base.get("allow_extensions", []),
        privacy=base.get("privacy", "internal"),
        credential_scrubbing=extraction.get("credential_scrubbing", False),
    )

    # If we loaded parent manifest but subfolder has its own, merge overrides
    if own_manifest is not None and parent_manifest is not None:
        # Subfolder manifest overrides specific fields from parent
        parent_ext = parent_manifest.get("extraction", {})
        if not extraction.get("scope"):
            policy.scope = parent_ext.get("scope", "")
        if not base.get("allow_extensions") and parent_manifest.get("allow_extensions"):
            policy.allow_extensions = parent_manifest["allow_extensions"]

    # Also check parent's subfolders section for credential_scrubbing override
    if parent_manifest and not own_manifest:
        subfolder_name = folder_path.name
        parent_subfolders = parent_manifest.get("subfolders", {})
        if subfolder_name in parent_subfolders:
            sub_cfg = parent_subfolders[subfolder_name]
            if sub_cfg.get("credential_scrubbing"):
                policy.credential_scrubbing = True

    return policy
