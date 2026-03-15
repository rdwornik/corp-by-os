"""Reads routing_map.yaml and resolves provenance for a given MyWork folder."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


@dataclass
class RouteInfo:
    """Resolved routing info for a MyWork folder."""

    vault_target: str
    provenance_scope: str
    content_origin: str
    source_category: str
    routing_confidence: float


class RoutingError(Exception):
    """Raised when a folder cannot be resolved to a route."""


def _load_routing_map(routing_map_path: Path) -> dict[str, Any]:
    """Load and return routing_map.yaml contents."""
    with open(routing_map_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _invert_provenance_map(provenance_map: dict[str, list[str]]) -> dict[str, str]:
    """Build source_category -> provenance_scope lookup."""
    result: dict[str, str] = {}
    for scope, categories in provenance_map.items():
        for cat in categories:
            result[cat] = scope
    return result


def resolve_route(
    folder_path: Path,
    routing_map: dict[str, Any],
    mywork_root: Path | None = None,
) -> RouteInfo:
    """Resolve a MyWork folder path to routing info.

    Logic:
    1. Extract top-level folder name from path relative to mywork_root
    2. Look up in routes section
    3. Check for subfolder-specific content_type overrides
    4. Map source_category -> provenance_scope via provenance_map
    """
    routes = routing_map.get("routes", {})
    provenance_map = routing_map.get("provenance_map", {})

    folder_path = folder_path.resolve()
    if mywork_root is not None:
        mywork_root = mywork_root.resolve()

    # Find relative parts from mywork_root
    if mywork_root and folder_path != mywork_root:
        try:
            rel = folder_path.relative_to(mywork_root)
        except ValueError as exc:
            raise RoutingError(
                f"Folder {folder_path} is not under MyWork root {mywork_root}"
            ) from exc
        parts = rel.parts
    else:
        parts = (folder_path.name,)

    top_folder = parts[0]
    route = routes.get(top_folder)
    if route is None:
        raise RoutingError(
            f"No route defined for '{top_folder}'. Known routes: {list(routes.keys())}"
        )

    vault_target = route.get("vault_target") or ""
    default_provenance = route.get("provenance", "")

    # Check subfolder overrides
    source_category = default_provenance
    if len(parts) > 1:
        subfolder_name = parts[1]
        subfolders = route.get("subfolders", {})
        if subfolder_name in subfolders:
            sub_cfg = subfolders[subfolder_name]
            source_category = sub_cfg.get("content_type", default_provenance)

    # Resolve provenance scope via provenance_map if available
    category_to_scope = _invert_provenance_map(provenance_map)
    provenance_scope = category_to_scope.get(source_category, default_provenance)

    return RouteInfo(
        vault_target=vault_target,
        provenance_scope=provenance_scope,
        content_origin="mywork",
        source_category=source_category,
        routing_confidence=1.0,
    )
