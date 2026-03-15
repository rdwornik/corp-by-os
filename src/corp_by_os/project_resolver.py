"""Resolve project references to concrete paths.

Handles: "Lenzing" -> project_id "lenzing_planning" -> paths in OneDrive + Vault.

Fuzzy matching via simple token scoring — no external libraries needed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from corp_by_os.config import get_config
from corp_by_os.models import ResolvedProject, VaultZone

logger = logging.getLogger(__name__)


def _score_match(query: str, folder_name: str) -> float:
    """Score how well a query matches a folder name.

    Scoring:
        1.0  — exact match (case-insensitive)
        0.9  — client prefix match (query matches part before first _)
        0.8  — prefix match (folder starts with query)
        0.6  — substring match (query found anywhere in folder)
        0.0  — no match
    """
    q = query.lower().replace("-", "_").replace(" ", "_")
    f = folder_name.lower()

    # Exact match
    if q == f:
        return 1.0

    # Client prefix — query matches the client slug (before first underscore)
    client_slug = f.split("_")[0]
    if q == client_slug:
        return 0.9

    # Prefix match
    if f.startswith(q):
        return 0.8

    # Substring match
    if q in f:
        return 0.6

    return 0.0


def list_all_project_ids() -> list[str]:
    """List all project folder names from OneDrive (original casing)."""
    cfg = get_config()
    if not cfg.projects_root.exists():
        return []

    return sorted(
        [
            f.name
            for f in cfg.projects_root.iterdir()
            if f.is_dir() and not f.name.startswith((".", "_"))
        ]
    )


def resolve_project(name_or_id: str) -> ResolvedProject | None:
    """Fuzzy-resolve a project name/id to paths.

    Returns the best match, or None if no match found.
    Logs a warning if multiple projects match with the same score.
    """
    cfg = get_config()
    folders = list_all_project_ids()

    if not folders:
        logger.warning("No project folders found in %s", cfg.projects_root)
        return None

    # Score all folders
    scored: list[tuple[float, str]] = []
    for folder_name in folders:
        score = _score_match(name_or_id, folder_name)
        if score > 0:
            scored.append((score, folder_name))

    if not scored:
        return None

    # Sort by score descending, then alphabetically for ties
    scored.sort(key=lambda x: (-x[0], x[1]))

    best_score, best_name = scored[0]

    # Check for ambiguous matches (multiple at same score)
    same_score = [name for s, name in scored if s == best_score]
    if len(same_score) > 1:
        logger.warning(
            "Ambiguous match for '%s': %s (using first alphabetically)",
            name_or_id,
            same_score,
        )

    # Build resolved paths
    onedrive_path = cfg.projects_root / best_name
    vault_dir = cfg.vault_path / VaultZone.PROJECTS.value / best_name
    vault_path = vault_dir if vault_dir.exists() else None

    return ResolvedProject(
        project_id=best_name.lower(),
        folder_name=best_name,
        onedrive_path=onedrive_path if onedrive_path.exists() else None,
        vault_path=vault_path,
        score=best_score,
    )


def get_onedrive_path(project_id: str) -> Path | None:
    """Get the OneDrive path for a project by exact folder name match."""
    cfg = get_config()
    # Try exact match first (case-insensitive scan)
    for folder in cfg.projects_root.iterdir():
        if folder.is_dir() and folder.name.lower() == project_id.lower():
            return folder
    return None


def get_vault_path(project_id: str) -> Path | None:
    """Get the vault path for a project."""
    cfg = get_config()
    path = cfg.vault_path / VaultZone.PROJECTS.value / project_id
    return path if path.exists() else None
