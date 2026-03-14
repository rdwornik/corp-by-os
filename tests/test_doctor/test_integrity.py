"""Tests for system integrity checks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from corp_by_os.doctor.integrity import (
    IntegrityReport,
    _check_config_files,
    _check_inbox,
    _check_mywork_structure,
    _check_ops_db,
    _check_registry_paths,
    _check_vault_index,
    check_all,
)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")


def _make_mywork(tmp_path: Path) -> Path:
    """Create a valid MyWork structure."""
    mywork = tmp_path / "mywork"
    for folder in [
        "00_Inbox", "10_Projects", "20_Extra_Initiatives",
        "30_Templates", "50_RFP", "60_Source_Library",
        "70_Admin", "90_System",
    ]:
        (mywork / folder).mkdir(parents=True, exist_ok=True)
    return mywork


def _make_index_db(path: Path, note_count: int = 0) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT)",
    )
    for i in range(note_count):
        conn.execute("INSERT INTO notes (title) VALUES (?)", (f"Note {i}",))
    conn.commit()
    conn.close()


def _make_ops_db(path: Path, assets: list[tuple[str, str]] | None = None) -> None:
    """Create ops.db with optional assets [(path, status), ...]."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'discovered'
        )""",
    )
    for asset_path, status in (assets or []):
        conn.execute(
            "INSERT INTO assets (path, status) VALUES (?, ?)",
            (asset_path, status),
        )
    conn.commit()
    conn.close()


# --- Config files ---


class TestCheckConfigFiles:
    def test_valid_configs(self, tmp_path: Path) -> None:
        """Config files present and valid -> passed."""
        report = IntegrityReport()
        registry = tmp_path / "registry.yaml"
        routing = tmp_path / "routing.yaml"
        _write_yaml(registry, {"series": {"test": {}}})
        _write_yaml(routing, {"folders": {}})

        _check_config_files(report, registry, routing)
        assert report.checks_passed == 2
        assert report.checks_failed == 0

    def test_missing_routing_map(self, tmp_path: Path) -> None:
        """Missing routing_map -> error."""
        report = IntegrityReport()
        registry = tmp_path / "registry.yaml"
        _write_yaml(registry, {"series": {}})

        _check_config_files(report, registry, tmp_path / "missing.yaml")
        assert report.checks_failed == 1
        assert any(i.description == "routing_map.yaml missing" for i in report.issues)

    def test_missing_registry(self, tmp_path: Path) -> None:
        """Missing content_registry -> error."""
        report = IntegrityReport()
        routing = tmp_path / "routing.yaml"
        _write_yaml(routing, {"folders": {}})

        _check_config_files(report, tmp_path / "missing.yaml", routing)
        assert report.checks_failed == 1
        assert any("content_registry" in i.description for i in report.issues)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        """Broken YAML -> error."""
        report = IntegrityReport()
        registry = tmp_path / "registry.yaml"
        registry.write_text(": [broken yaml", encoding="utf-8")
        routing = tmp_path / "routing.yaml"
        _write_yaml(routing, {"folders": {}})

        _check_config_files(report, registry, routing)
        assert report.checks_failed >= 1
        assert any("parse error" in i.description for i in report.issues)

    def test_registry_missing_series(self, tmp_path: Path) -> None:
        """Registry without 'series' key -> warning."""
        report = IntegrityReport()
        registry = tmp_path / "registry.yaml"
        _write_yaml(registry, {"version": "1.0"})
        routing = tmp_path / "routing.yaml"
        _write_yaml(routing, {"folders": {}})

        _check_config_files(report, registry, routing)
        assert report.checks_warned == 1


# --- Registry paths ---


class TestCheckRegistryPaths:
    def test_valid_destinations(self, tmp_path: Path) -> None:
        """All registry destinations exist -> passed."""
        mywork = _make_mywork(tmp_path)
        registry = tmp_path / "registry.yaml"
        (mywork / "60_Source_Library" / "Training").mkdir(parents=True)
        _write_yaml(registry, {
            "series": {
                "test_series": {"destination": "60_Source_Library/Training"},
            },
        })

        report = IntegrityReport()
        _check_registry_paths(report, mywork, registry)
        assert report.checks_passed == 1
        assert report.checks_warned == 0

    def test_missing_destination(self, tmp_path: Path) -> None:
        """Missing destination folder -> warning with fix hint."""
        mywork = _make_mywork(tmp_path)
        registry = tmp_path / "registry.yaml"
        _write_yaml(registry, {
            "series": {
                "missing_series": {"destination": "99_NonExistent/Folder"},
            },
        })

        report = IntegrityReport()
        _check_registry_paths(report, mywork, registry)
        assert report.checks_warned == 1
        assert report.issues[0].fix_hint is not None


# --- Ops DB ---


class TestCheckOpsDb:
    def test_no_ops_db(self, tmp_path: Path) -> None:
        """No ops.db yet -> info, not error."""
        mywork = _make_mywork(tmp_path)
        report = IntegrityReport()
        _check_ops_db(report, mywork, tmp_path / "nonexistent.db")
        assert report.checks_passed == 1
        assert report.checks_failed == 0

    def test_all_assets_present(self, tmp_path: Path) -> None:
        """Assets in ops.db exist on disk -> passed."""
        mywork = _make_mywork(tmp_path)
        test_file = mywork / "10_Projects" / "doc.txt"
        test_file.write_text("test", encoding="utf-8")

        ops_db = tmp_path / "ops.db"
        _make_ops_db(ops_db, [("10_Projects/doc.txt", "discovered")])

        report = IntegrityReport()
        _check_ops_db(report, mywork, ops_db)
        assert report.checks_passed == 1
        assert report.checks_warned == 0

    def test_missing_assets(self, tmp_path: Path) -> None:
        """Assets in ops.db but not on disk -> warning."""
        mywork = _make_mywork(tmp_path)
        ops_db = tmp_path / "ops.db"
        _make_ops_db(ops_db, [("10_Projects/missing.txt", "discovered")])

        report = IntegrityReport()
        _check_ops_db(report, mywork, ops_db)
        assert report.checks_warned == 1

    def test_deleted_assets_ignored(self, tmp_path: Path) -> None:
        """Assets with status 'deleted' are not checked."""
        mywork = _make_mywork(tmp_path)
        ops_db = tmp_path / "ops.db"
        _make_ops_db(ops_db, [("gone.txt", "deleted")])

        report = IntegrityReport()
        _check_ops_db(report, mywork, ops_db)
        assert report.checks_passed == 1
        assert report.checks_warned == 0

    def test_pending_assets_info(self, tmp_path: Path) -> None:
        """Pending assets produce info issue."""
        mywork = _make_mywork(tmp_path)
        pending_file = mywork / "new.txt"
        pending_file.write_text("test", encoding="utf-8")
        ops_db = tmp_path / "ops.db"
        _make_ops_db(ops_db, [("new.txt", "pending")])

        report = IntegrityReport()
        _check_ops_db(report, mywork, ops_db)
        assert any("pending" in i.description for i in report.issues)


# --- Vault / Index ---


class TestCheckVaultIndex:
    def test_index_missing(self, tmp_path: Path) -> None:
        """Missing index.db -> error."""
        vault = tmp_path / "vault"
        vault.mkdir()
        report = IntegrityReport()
        _check_vault_index(report, vault, tmp_path / "missing.db")
        assert report.checks_failed == 1

    def test_index_in_sync(self, tmp_path: Path) -> None:
        """Index count matches vault notes -> passed."""
        vault = tmp_path / "vault"
        sources = vault / "02_sources"
        sources.mkdir(parents=True)
        for i in range(3):
            (sources / f"note_{i}.md").write_text(f"Note {i}", encoding="utf-8")

        db_path = tmp_path / "index.db"
        _make_index_db(db_path, note_count=3)

        report = IntegrityReport()
        _check_vault_index(report, vault, db_path)
        assert report.checks_passed >= 1
        assert report.checks_warned == 0

    def test_index_drift_large(self, tmp_path: Path) -> None:
        """Large drift between index and vault -> warning."""
        vault = tmp_path / "vault"
        sources = vault / "02_sources"
        sources.mkdir(parents=True)
        # 60 vault notes but only 5 indexed
        for i in range(60):
            (sources / f"note_{i}.md").write_text(f"Note {i}", encoding="utf-8")

        db_path = tmp_path / "index.db"
        _make_index_db(db_path, note_count=5)

        report = IntegrityReport()
        _check_vault_index(report, vault, db_path)
        assert report.checks_warned == 1
        assert any("drift" in i.description.lower() for i in report.issues)

    def test_skips_synthesis_files(self, tmp_path: Path) -> None:
        """synthesis.md and index.md are excluded from vault count."""
        vault = tmp_path / "vault"
        sources = vault / "02_sources"
        sources.mkdir(parents=True)
        (sources / "real_note.md").write_text("Content", encoding="utf-8")
        (sources / "synthesis.md").write_text("# Summary", encoding="utf-8")
        (sources / "index.md").write_text("# Index", encoding="utf-8")

        db_path = tmp_path / "index.db"
        _make_index_db(db_path, note_count=1)

        report = IntegrityReport()
        _check_vault_index(report, vault, db_path)
        assert report.checks_passed >= 1
        assert report.checks_warned == 0


# --- MyWork structure ---


class TestCheckMyworkStructure:
    def test_all_folders_exist(self, tmp_path: Path) -> None:
        """All required folders exist -> passed."""
        mywork = _make_mywork(tmp_path)
        report = IntegrityReport()
        _check_mywork_structure(report, mywork)
        assert report.checks_passed == 8
        assert report.checks_failed == 0

    def test_missing_folder(self, tmp_path: Path) -> None:
        """Missing required folder -> error."""
        mywork = tmp_path / "mywork"
        mywork.mkdir()
        # Only create some folders
        (mywork / "00_Inbox").mkdir()
        (mywork / "10_Projects").mkdir()

        report = IntegrityReport()
        _check_mywork_structure(report, mywork)
        assert report.checks_failed >= 1
        assert any("missing" in i.description.lower() for i in report.issues)


# --- Inbox ---


class TestCheckInbox:
    def test_empty_inbox(self, tmp_path: Path) -> None:
        """Empty inbox -> no issues."""
        mywork = _make_mywork(tmp_path)
        report = IntegrityReport()
        _check_inbox(report, mywork)
        assert not report.issues

    def test_files_in_inbox(self, tmp_path: Path) -> None:
        """Files in inbox -> info with hint."""
        mywork = _make_mywork(tmp_path)
        (mywork / "00_Inbox" / "new_file.pdf").write_text("data", encoding="utf-8")

        report = IntegrityReport()
        _check_inbox(report, mywork)
        assert len(report.issues) == 1
        assert "awaiting ingest" in report.issues[0].description

    def test_skips_system_files(self, tmp_path: Path) -> None:
        """System files in inbox are ignored."""
        mywork = _make_mywork(tmp_path)
        (mywork / "00_Inbox" / "_triage_log.jsonl").write_text("{}", encoding="utf-8")
        (mywork / "00_Inbox" / "folder_manifest.yaml").write_text("", encoding="utf-8")

        report = IntegrityReport()
        _check_inbox(report, mywork)
        assert not report.issues

    def test_quarantine_dirs(self, tmp_path: Path) -> None:
        """Files in _Unmatched -> info."""
        mywork = _make_mywork(tmp_path)
        unmatched = mywork / "00_Inbox" / "_Unmatched"
        unmatched.mkdir()
        (unmatched / "mystery.docx").write_text("data", encoding="utf-8")

        report = IntegrityReport()
        _check_inbox(report, mywork)
        assert len(report.issues) == 1
        assert "_Unmatched" in report.issues[0].description

    def test_no_inbox_dir(self, tmp_path: Path) -> None:
        """No inbox directory -> no crash."""
        mywork = tmp_path / "mywork"
        mywork.mkdir()
        report = IntegrityReport()
        _check_inbox(report, mywork)
        assert not report.issues


# --- Full check_all ---


class TestCheckAll:
    def test_healthy_system(self, tmp_path: Path) -> None:
        """Full healthy system -> report.healthy == True."""
        mywork = _make_mywork(tmp_path)
        vault = tmp_path / "vault"
        (vault / "02_sources").mkdir(parents=True)

        registry = tmp_path / "registry.yaml"
        _write_yaml(registry, {"series": {}})
        routing = mywork / "90_System" / "routing_map.yaml"
        _write_yaml(routing, {"folders": {}})

        index_db = tmp_path / "index.db"
        _make_index_db(index_db, note_count=0)
        ops_db = tmp_path / "ops.db"

        report = check_all(
            mywork_root=mywork,
            vault_root=vault,
            index_db_path=index_db,
            ops_db_path=ops_db,
            registry_path=registry,
            routing_map_path=routing,
        )
        assert report.healthy
        assert report.checks_passed >= 1

    def test_system_with_errors(self, tmp_path: Path) -> None:
        """System with issues -> report.healthy == False."""
        mywork = tmp_path / "mywork"
        mywork.mkdir()  # Missing required subfolders

        report = check_all(
            mywork_root=mywork,
            vault_root=tmp_path / "vault",
            index_db_path=tmp_path / "missing.db",
            ops_db_path=tmp_path / "ops.db",
            registry_path=tmp_path / "missing_reg.yaml",
            routing_map_path=tmp_path / "missing_routing.yaml",
        )
        assert not report.healthy
        assert report.checks_failed >= 1
