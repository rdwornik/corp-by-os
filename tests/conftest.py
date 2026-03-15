"""Shared test fixtures for corp-by-os."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.config import get_config


@pytest.fixture()
def tmp_vault(tmp_path: Path) -> Path:
    """Create a temporary vault structure mimicking Obsidian."""
    vault = tmp_path / "vault"
    # Create zone directories
    for zone in ["00_dashboards", "01_projects", "02_sources", "03_playbooks"]:
        (vault / zone).mkdir(parents=True)

    # Create a sample project
    proj_dir = vault / "01_projects" / "lenzing_planning"
    proj_dir.mkdir(parents=True)

    info = {
        "project_id": "lenzing_planning",
        "client": "Lenzing AG",
        "status": "active",
        "products": ["Blue Yonder Demand Planning"],
        "topics": ["Supply Chain Planning", "Demand Planning"],
        "domains": ["Product", "Delivery & Implementation"],
        "files_processed": 141,
        "facts_count": 992,
        "last_extracted": "2026-03-06",
    }
    (proj_dir / "project-info.yaml").write_text(
        yaml.dump(info, default_flow_style=False),
        encoding="utf-8",
    )

    # Create a sample note with frontmatter
    note_content = """---
title: Lenzing Discovery Workshop
document_type: presentation
source_type: internal
---
# Lenzing Discovery Workshop

Notes from the discovery session.
"""
    (proj_dir / "index.md").write_text(note_content, encoding="utf-8")

    return vault


@pytest.fixture()
def tmp_projects(tmp_path: Path) -> Path:
    """Create a temporary OneDrive projects structure."""
    projects = tmp_path / "projects"
    projects.mkdir()

    for name in [
        "Lenzing_Planning",
        "Honda_Planning",
        "Stellantis_Mopar_E2E",
        "Zabka_CatMan",
        "Zabka_Retail",
    ]:
        (projects / name).mkdir()

    return projects


@pytest.fixture()
def app_config(
    tmp_vault: Path, tmp_projects: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Set up AppConfig pointing to temp directories."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_vault))
    monkeypatch.setenv("MYWORK_ROOT", str(tmp_path / "mywork"))
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_projects))
    monkeypatch.setenv("TEMPLATES_ROOT", str(tmp_path / "templates"))
    monkeypatch.setenv("ARCHIVE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("APP_DATA_PATH", str(tmp_path / "appdata"))

    # Clear the lru_cache so get_config() picks up new env vars
    get_config.cache_clear()

    yield get_config()

    # Clean up cache after test
    get_config.cache_clear()
