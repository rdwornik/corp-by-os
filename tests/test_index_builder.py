"""Tests for index_builder module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.index_builder import (
    _connect,
    _ensure_schema,
    get_index_stats,
    rebuild_index,
    update_project,
)


# --- Fixtures ---


@pytest.fixture()
def index_env(app_config, tmp_vault: Path, tmp_projects: Path, tmp_path: Path):
    """Set up environment with projects that have project-info and facts."""
    # Add _knowledge/project-info.yaml to one project
    lenzing = tmp_projects / "Lenzing_Planning"
    knowledge = lenzing / "_knowledge"
    knowledge.mkdir(parents=True)

    info = {
        "project": "Lenzing_Planning",
        "status": "active",
        "files_processed": 9,
        "products": ["Planning", "WMS"],
        "topics": ["Demand Planning", "SAP Integration", "Security"],
        "people": ["Jane Doe"],
        "rendered_at": "2026-03-08",
    }
    (knowledge / "project-info.yaml").write_text(
        yaml.dump(info), encoding="utf-8",
    )

    # Add facts.yaml
    facts = {
        "project": "Lenzing_Planning",
        "total_facts": 3,
        "facts": [
            {
                "fact": "SAP integration requires custom middleware for real-time data sync.",
                "source": "note-001",
                "source_title": "Integration Architecture",
                "topics": ["SAP Integration", "Architecture"],
            },
            {
                "fact": "Demand planning horizon is 18 months with weekly granularity.",
                "source": "note-002",
                "source_title": "Planning Requirements",
                "topics": ["Demand Planning"],
            },
            {
                "fact": "Security compliance requires SOC2 Type II certification.",
                "source": "note-003",
                "source_title": "Security Review",
                "topics": ["Security"],
            },
        ],
    }
    (knowledge / "facts.yaml").write_text(
        yaml.dump(facts, default_flow_style=False), encoding="utf-8",
    )

    # Honda has no _knowledge, just a folder
    # (already created by tmp_projects fixture)

    return tmp_path


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "appdata" / "index.db"


# --- Test: Schema ---


class TestSchema:
    def test_creates_tables(self, db_path: Path) -> None:
        conn = _connect(db_path)
        _ensure_schema(conn)

        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "projects" in table_names
        assert "facts" in table_names
        assert "meta" in table_names
        conn.close()

    def test_schema_idempotent(self, db_path: Path) -> None:
        conn = _connect(db_path)
        _ensure_schema(conn)
        _ensure_schema(conn)  # should not raise
        conn.close()


# --- Test: Rebuild ---


class TestRebuild:
    def test_indexes_projects(self, index_env, db_path: Path) -> None:
        stats = rebuild_index(db_path)
        assert stats.projects_indexed >= 5  # from tmp_projects fixture
        assert stats.rebuild_duration >= 0

    def test_indexes_facts(self, index_env, db_path: Path) -> None:
        stats = rebuild_index(db_path)
        assert stats.facts_indexed == 3  # Lenzing has 3 facts

    def test_projects_without_facts(self, index_env, db_path: Path) -> None:
        """Projects without facts.yaml should still be indexed."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        row = conn.execute(
            "SELECT facts_count FROM projects WHERE project_id = ?",
            ("honda_planning",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 0

    def test_project_metadata_from_onedrive(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        conn = _connect(db_path)
        row = conn.execute(
            "SELECT client, status, products FROM projects WHERE project_id = ?",
            ("lenzing_planning",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert "Lenzing" in row[0]  # from folder name or vault project-info
        assert row[1] == "active"
        assert "Planning" in row[2]

    def test_meta_updated(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        meta = get_index_stats(db_path)
        assert "last_rebuild" in meta
        assert "total_projects" in meta
        assert "total_facts" in meta
        assert meta["total_facts"] == "3"

    def test_rebuild_is_idempotent(self, index_env, db_path: Path) -> None:
        stats1 = rebuild_index(db_path)
        stats2 = rebuild_index(db_path)
        assert stats1.projects_indexed == stats2.projects_indexed
        assert stats1.facts_indexed == stats2.facts_indexed

    def test_vault_project_merged(self, index_env, db_path: Path, tmp_vault: Path) -> None:
        """Vault project (lenzing_planning in 01_projects) should merge with OneDrive."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        row = conn.execute(
            "SELECT vault_path FROM projects WHERE project_id = 'lenzing_planning'",
        ).fetchone()
        conn.close()
        assert row is not None
        # vault_path should be set since tmp_vault has 01_projects/lenzing_planning
        assert row[0] is not None


# --- Test: Update Project ---


class TestUpdateProject:
    def test_update_existing(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        ok = update_project("lenzing_planning", db_path)
        assert ok is True

    def test_update_nonexistent(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        ok = update_project("nonexistent_xyz", db_path)
        assert ok is False

    def test_update_preserves_others(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        # Update Lenzing, Honda should still be there
        update_project("lenzing_planning", db_path)
        conn = _connect(db_path)
        row = conn.execute(
            "SELECT project_id FROM projects WHERE project_id = 'honda_planning'",
        ).fetchone()
        conn.close()
        assert row is not None


# --- Test: FTS triggers ---


class TestFTS:
    def test_fts_populated(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT * FROM facts_fts WHERE facts_fts MATCH '\"SAP\"'",
        ).fetchall()
        conn.close()
        assert len(rows) >= 1

    def test_fts_returns_matching_facts(self, index_env, db_path: Path) -> None:
        rebuild_index(db_path)
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT fact FROM facts_fts WHERE facts_fts MATCH '\"demand\"'",
        ).fetchall()
        conn.close()
        assert any("Demand" in r[0] for r in rows)
