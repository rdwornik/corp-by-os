"""Configuration — loads from .env + agents.yaml.

Single frozen AppConfig dataclass. Cached via lru_cache.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class AppConfig:
    """Application configuration — immutable after creation."""

    vault_path: Path
    projects_root: Path
    archive_root: Path
    app_data_path: Path
    repo_path: Path
    agents: dict[str, Any] = field(default_factory=dict)


def _expand_path(raw: str) -> Path:
    """Expand env vars and resolve path."""
    return Path(os.path.expandvars(raw)).resolve()


def _load_agents(repo_path: Path) -> dict[str, Any]:
    """Load agent registry from config/agents.yaml."""
    agents_file = repo_path / "config" / "agents.yaml"
    if agents_file.exists():
        try:
            with open(agents_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return data.get("agents", {})
        except yaml.YAMLError as e:
            import logging
            logging.getLogger(__name__).warning("Failed to parse agents.yaml: %s", e)
            return {}
    return {}


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Load and cache application config.

    Reads from .env file in repo root, falls back to environment variables.
    """
    repo_path = Path(__file__).resolve().parent.parent.parent
    load_dotenv(repo_path / ".env")

    vault_path = os.environ.get(
        "VAULT_PATH",
        r"C:\Users\1028120\Documents\ObsidianVault",
    )
    projects_root = os.environ.get(
        "PROJECTS_ROOT",
        r"C:\Users\1028120\OneDrive - Blue Yonder\MyWork\10_Projects",
    )
    archive_root = os.environ.get(
        "ARCHIVE_ROOT",
        r"C:\Users\1028120\OneDrive - Blue Yonder\MyWork\80_Archive",
    )
    app_data_path = os.environ.get(
        "APP_DATA_PATH",
        os.path.expandvars(r"%LOCALAPPDATA%\corp-by-os"),
    )

    return AppConfig(
        vault_path=_expand_path(vault_path),
        projects_root=_expand_path(projects_root),
        archive_root=_expand_path(archive_root),
        app_data_path=_expand_path(app_data_path),
        repo_path=repo_path,
        agents=_load_agents(repo_path),
    )
