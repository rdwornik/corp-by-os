"""Tests for index_builder module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.index_builder import (
    _connect,
    _ensure_schema,
    _index_cke_notes,
    _parse_frontmatter,
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
        yaml.dump(info),
        encoding="utf-8",
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
        yaml.dump(facts, default_flow_style=False),
        encoding="utf-8",
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
        assert "notes" in table_names, "notes table missing — code was deleted"
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


# --- Test: Notes indexing (regression — never delete) ---


@pytest.fixture()
def notes_env(app_config, tmp_vault: Path, tmp_projects: Path, tmp_path: Path):
    """Set up vault with CKE-generated notes in 02_sources and 04_evergreen."""
    # Create notes in 02_sources with frontmatter
    sources_proj = tmp_vault / "02_sources" / "lenzing_planning"
    sources_proj.mkdir(parents=True, exist_ok=True)

    note1 = """\
---
title: Platform Architecture Overview
project: lenzing_planning
client: Lenzing AG
type: extract
source_type: internal
topics:
  - Architecture
  - Platform
products:
  - Blue Yonder Platform
domains:
  - Product
---
# Platform Architecture Overview

Key findings from the architecture review.
"""
    (sources_proj / "platform-architecture.md").write_text(note1, encoding="utf-8")

    note2 = """\
---
title: Security Compliance Report
project: lenzing_planning
client: Lenzing AG
type: extract
source_type: internal
topics:
  - Security
  - Compliance
products:
  - Blue Yonder WMS
---
# Security Compliance Report

SOC2 requirements and audit findings.
"""
    (sources_proj / "security-compliance.md").write_text(note2, encoding="utf-8")

    # Create note in 04_evergreen/_generated
    evergreen = tmp_vault / "04_evergreen" / "_generated"
    evergreen.mkdir(parents=True, exist_ok=True)

    note3 = """\
---
title: Supply Chain Best Practices
type: evergreen
topics:
  - Supply Chain
  - Best Practices
---
# Supply Chain Best Practices

Cross-project patterns in supply chain planning.
"""
    (evergreen / "supply-chain-best-practices.md").write_text(note3, encoding="utf-8")

    # Also add project-info and facts for index_env compatibility
    lenzing = tmp_projects / "Lenzing_Planning"
    knowledge = lenzing / "_knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)

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
        yaml.dump(info),
        encoding="utf-8",
    )

    facts = {
        "project": "Lenzing_Planning",
        "total_facts": 1,
        "facts": [
            {
                "fact": "SAP integration requires custom middleware.",
                "source": "note-001",
                "source_title": "Integration Architecture",
                "topics": ["SAP Integration"],
            },
        ],
    }
    (knowledge / "facts.yaml").write_text(
        yaml.dump(facts, default_flow_style=False),
        encoding="utf-8",
    )

    return tmp_path


class TestNotesIndexing:
    """Regression tests — these ensure notes indexing can never be silently deleted."""

    def test_rebuild_indexes_notes(self, notes_env, db_path: Path) -> None:
        """rebuild_index must populate the notes table from vault markdown."""
        stats = rebuild_index(db_path)
        assert stats.notes_indexed >= 3, (
            f"Expected >= 3 notes indexed, got {stats.notes_indexed}. "
            "Notes indexing code may have been deleted."
        )

    def test_notes_table_has_rows(self, notes_env, db_path: Path) -> None:
        """The notes table must contain rows after rebuild."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        conn.close()
        assert count >= 3, f"notes table has {count} rows, expected >= 3"

    def test_notes_fts_searchable(self, notes_env, db_path: Path) -> None:
        """notes_fts must return results for indexed note titles."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT title FROM notes_fts WHERE notes_fts MATCH '\"Platform\"'",
        ).fetchall()
        conn.close()
        assert len(rows) >= 1, "FTS search for 'Platform' returned 0 results"
        assert any("Platform" in r[0] for r in rows)

    def test_notes_fts_searches_topics(self, notes_env, db_path: Path) -> None:
        """notes_fts must index topics for search."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT title FROM notes_fts WHERE notes_fts MATCH '\"Security\"'",
        ).fetchall()
        conn.close()
        assert len(rows) >= 1, "FTS search for 'Security' in topics returned 0 results"

    def test_notes_project_id_set(self, notes_env, db_path: Path) -> None:
        """Notes with project field should have project_id set."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT project_id, title FROM notes WHERE project_id = 'lenzing_planning'",
        ).fetchall()
        conn.close()
        assert len(rows) >= 2, f"Expected >= 2 Lenzing notes, got {len(rows)}"

    def test_evergreen_notes_indexed(self, notes_env, db_path: Path) -> None:
        """Notes in 04_evergreen/_generated must also be indexed."""
        rebuild_index(db_path)
        conn = _connect(db_path)
        rows = conn.execute(
            "SELECT title FROM notes WHERE title LIKE '%Supply Chain%'",
        ).fetchall()
        conn.close()
        assert len(rows) >= 1, "Evergreen note not indexed"

    def test_meta_total_notes(self, notes_env, db_path: Path) -> None:
        """meta table must track total_notes count."""
        rebuild_index(db_path)
        meta = get_index_stats(db_path)
        assert "total_notes" in meta, "total_notes missing from meta table"
        assert int(meta["total_notes"]) >= 3

    def test_parse_frontmatter_valid(self, tmp_path: Path) -> None:
        """_parse_frontmatter must extract YAML from markdown files."""
        md = tmp_path / "test.md"
        md.write_text("---\ntitle: Test Note\ntopics:\n  - A\n---\nBody text.\n", encoding="utf-8")
        fm = _parse_frontmatter(md)
        assert fm is not None
        assert fm["title"] == "Test Note"
        assert fm["topics"] == ["A"]

    def test_parse_frontmatter_no_yaml(self, tmp_path: Path) -> None:
        """_parse_frontmatter returns None for files without frontmatter."""
        md = tmp_path / "no_fm.md"
        md.write_text("# Just a heading\n\nNo frontmatter here.\n", encoding="utf-8")
        assert _parse_frontmatter(md) is None

    def test_parse_frontmatter_missing_file(self, tmp_path: Path) -> None:
        """_parse_frontmatter returns None for missing files."""
        assert _parse_frontmatter(tmp_path / "nonexistent.md") is None
