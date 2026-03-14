"""Content registry loader and pattern matcher.

Reads content_registry.yaml and provides methods to match files
against known series, destination rules, and client patterns.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def get_content_registry_path() -> Path:
    """Default content_registry.yaml path from config."""
    from corp_by_os.config import get_config

    return get_config().mywork_root / "90_System" / "content_registry.yaml"


@dataclass(frozen=True)
class RegistryMatch:
    """Result of matching a file against the registry."""

    matched: bool
    destination: str | None
    series_id: str | None = None
    rule_name: str | None = None
    confidence: float = 0.0
    method: str = "none"  # 'series' | 'rule' | 'client' | 'none'
    metadata: dict = field(default_factory=dict)


class ContentRegistry:
    """Loads and queries the content registry.

    Match order (first match with highest confidence wins):
    1. Series patterns — recurring content with known naming
    2. Client patterns — filenames containing client identifiers
    3. Destination rules — general rules by filename/extension
    4. Fallback — no match, low confidence
    """

    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path
        self._data: dict | None = None

    @property
    def data(self) -> dict:
        if self._data is None:
            with open(self.registry_path, encoding="utf-8") as f:
                self._data = yaml.safe_load(f)
            if not isinstance(self._data, dict):
                self._data = {}
        return self._data

    def reload(self) -> None:
        """Force reload from disk."""
        self._data = None

    # === Main matching API ===

    def match_file(
        self,
        filename: str,
        extension: str,
        folder_context: str | None = None,
    ) -> RegistryMatch:
        """Match a file against all registry rules.

        Returns the BEST match (highest confidence).
        """
        # 1. Series match (highest priority)
        series_match = self._match_series(filename)
        if series_match.matched:
            return series_match

        # 2. Client pattern match
        client_match = self._match_client(filename)
        if client_match.matched:
            return client_match

        # 3. Destination rule match
        rule_match = self._match_rules(filename, extension, folder_context)
        if rule_match.matched:
            return rule_match

        # 4. Fallback
        fallback = self.data.get("fallback", {})
        return RegistryMatch(
            matched=False,
            destination=fallback.get("unknown_destination"),
            confidence=0.0,
            method="none",
        )

    def match_folder(self, folder_name: str) -> RegistryMatch:
        """Match a folder name against registry for package routing.

        Checks series patterns and client patterns against folder name.
        """
        # Try series match on folder name
        series_match = self._match_series(folder_name)
        if series_match.matched:
            return series_match

        # Try client match
        client_match = self._match_client(folder_name)
        if client_match.matched:
            return client_match

        return RegistryMatch(matched=False, destination=None)

    # === Accessors ===

    def get_series(self, series_id: str) -> dict | None:
        """Get series definition by ID."""
        return self.data.get("series", {}).get(series_id)

    def get_all_series(self) -> dict:
        """Get all series definitions."""
        return self.data.get("series", {})

    def get_all_client_patterns(self) -> list[dict]:
        """Get all client patterns for project matching."""
        return self.data.get("client_patterns", [])

    def get_fallback_config(self) -> dict:
        """Get fallback routing configuration."""
        return self.data.get("fallback", {})

    # === Internal matching ===

    def _match_series(self, filename: str) -> RegistryMatch:
        """Check filename against all series naming patterns."""
        for series_id, series_def in self.data.get("series", {}).items():
            patterns = series_def.get("naming_patterns", [])
            for pattern in patterns:
                if fnmatch(filename, pattern):
                    return RegistryMatch(
                        matched=True,
                        destination=series_def.get("destination"),
                        series_id=series_id,
                        confidence=0.95,
                        method="series",
                        metadata=series_def.get("default_metadata", {}),
                    )
        return RegistryMatch(matched=False, destination=None)

    def _match_client(self, filename: str) -> RegistryMatch:
        """Check filename against client regex patterns."""
        for cp in self.data.get("client_patterns", []):
            pattern = cp.get("pattern", "")
            project = cp.get("project", "")
            if not pattern or not project:
                continue
            try:
                if re.search(pattern, filename, re.IGNORECASE):
                    return RegistryMatch(
                        matched=True,
                        destination=f"10_Projects/{project}",
                        confidence=0.80,
                        method="client",
                        metadata={"project": project},
                    )
            except re.error:
                logger.debug("Invalid client regex: %s", pattern)
        return RegistryMatch(matched=False, destination=None)

    def _match_rules(
        self,
        filename: str,
        extension: str,
        folder_context: str | None,
    ) -> RegistryMatch:
        """Check filename against destination rules."""
        ext_lower = extension.lower()
        if not ext_lower.startswith("."):
            ext_lower = f".{ext_lower}"

        for rule in self.data.get("destination_rules", []):
            match_spec = rule.get("match", {})

            # Extension check
            allowed_exts = match_spec.get("extensions", [])
            if allowed_exts and ext_lower not in allowed_exts:
                continue

            # Filename substring check
            filename_contains = match_spec.get("filename_contains", [])
            if filename_contains:
                if not any(sub in filename for sub in filename_contains):
                    continue

            # Folder hint check — boosts confidence when folder matches,
            # but does NOT reject files from other locations (e.g. Inbox).
            folder_hint = match_spec.get("folder_hint")
            confidence = 0.85
            if folder_hint and folder_context:
                if folder_hint in folder_context:
                    confidence = 0.90
                else:
                    confidence = 0.75

            return RegistryMatch(
                matched=True,
                destination=rule.get("destination"),
                rule_name=rule.get("name"),
                confidence=confidence,
                method="rule",
                metadata=rule.get("metadata", {}),
            )

        return RegistryMatch(matched=False, destination=None)
