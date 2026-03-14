"""Tests for ingest router — detect, match, route, record."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.ingest.router import (
    InboxItem,
    IngestResult,
    PackageIngestResult,
    compute_file_hash,
    finalize_file,
    get_staged_files,
    ingest_all,
    ingest_file,
    ingest_folder,
    scan_inbox,
)
from corp_by_os.ops.database import OpsDB
from corp_by_os.ops.registry import ContentRegistry


@pytest.fixture()
def mywork(tmp_path: Path) -> Path:
    """Create a minimal MyWork directory structure."""
    inbox = tmp_path / "00_Inbox"
    inbox.mkdir()
    (tmp_path / "10_Projects").mkdir()
    (tmp_path / "60_Source_Library" / "02_Training_Enablement" / "Cognitive_Friday").mkdir(parents=True)
    (tmp_path / "50_RFP" / "_databases").mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    """Create a test content_registry.yaml."""
    data = {
        "version": "1.0",
        "series": {
            "cognitive_friday": {
                "display_name": "Cognitive Friday",
                "destination": "60_Source_Library/02_Training_Enablement/Cognitive_Friday",
                "naming_patterns": [
                    "Cognitive_Friday*",
                    "CF_S[0-9]*",
                ],
                "expected_extensions": [".mp4", ".pptx"],
                "default_metadata": {
                    "source_category": "training",
                    "topics": ["Cognitive Planning"],
                },
            },
        },
        "destination_rules": [
            {
                "name": "RFP databases",
                "match": {
                    "filename_contains": ["RFP_Database"],
                    "extensions": [".xlsx", ".csv"],
                },
                "destination": "50_RFP/_databases",
                "metadata": {"source_category": "rfp"},
            },
        ],
        "client_patterns": [
            {"pattern": "Lenzing", "project": "Lenzing_Planning"},
        ],
        "fallback": {
            "unknown_destination": "00_Inbox/_Unmatched",
            "confidence_threshold": 0.75,
            "llm_escalation_threshold": 0.50,
        },
    }
    path = tmp_path / "content_registry.yaml"
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


@pytest.fixture()
def registry(registry_path: Path) -> ContentRegistry:
    return ContentRegistry(registry_path)


@pytest.fixture()
def ops(tmp_path: Path) -> OpsDB:
    db = OpsDB(db_path=tmp_path / "test_ops.db")
    _ = db.conn  # init schema
    yield db
    db.close()


# === scan_inbox ===


class TestScanInbox:
    def test_finds_files_in_inbox(self, mywork: Path) -> None:
        """scan_inbox returns loose files as InboxItems."""
        (mywork / "00_Inbox" / "report.pdf").write_bytes(b"pdf content")
        (mywork / "00_Inbox" / "deck.pptx").write_bytes(b"pptx content")

        items = scan_inbox(mywork)
        assert len(items) == 2
        assert all(not i.is_folder for i in items)
        names = {i.path.name for i in items}
        assert "report.pdf" in names
        assert "deck.pptx" in names

    def test_detects_folders(self, mywork: Path) -> None:
        """scan_inbox detects depth-1 folders as InboxItems."""
        sub = mywork / "00_Inbox" / "Workshop_Materials"
        sub.mkdir()
        (sub / "slides.pptx").write_bytes(b"slides")
        (sub / "notes.docx").write_bytes(b"notes")

        items = scan_inbox(mywork)
        assert len(items) == 1
        assert items[0].is_folder is True
        assert items[0].path.name == "Workshop_Materials"
        assert items[0].file_count == 2

    def test_folder_stats(self, mywork: Path) -> None:
        """Folder InboxItem has correct file_count, total_size, depth."""
        sub = mywork / "00_Inbox" / "Deep_Folder"
        nested = sub / "level1" / "level2"
        nested.mkdir(parents=True)
        (sub / "top.txt").write_bytes(b"1234567890")  # 10 bytes
        (nested / "deep.txt").write_bytes(b"12345")  # 5 bytes

        items = scan_inbox(mywork)
        folder = items[0]
        assert folder.is_folder is True
        assert folder.file_count == 2
        assert folder.total_size_bytes == 15
        assert folder.depth == 3  # level1/level2/deep.txt

    def test_mixed_files_and_folders(self, mywork: Path) -> None:
        """Inbox with both files and folders returns both types."""
        (mywork / "00_Inbox" / "loose.pdf").write_bytes(b"loose")
        sub = mywork / "00_Inbox" / "Pkg_Folder"
        sub.mkdir()
        (sub / "inside.pptx").write_bytes(b"inside")

        items = scan_inbox(mywork)
        assert len(items) == 2
        folders = [i for i in items if i.is_folder]
        files = [i for i in items if not i.is_folder]
        assert len(folders) == 1
        assert len(files) == 1
        assert folders[0].path.name == "Pkg_Folder"
        assert files[0].path.name == "loose.pdf"

    def test_files_inside_folders_not_listed_separately(self, mywork: Path) -> None:
        """Files inside depth-1 folders do NOT appear as separate items."""
        sub = mywork / "00_Inbox" / "My_Package"
        sub.mkdir()
        (sub / "a.pdf").write_bytes(b"a")
        (sub / "b.pdf").write_bytes(b"b")

        items = scan_inbox(mywork)
        assert len(items) == 1
        assert items[0].is_folder is True
        assert items[0].file_count == 2

    def test_skips_infrastructure(self, mywork: Path) -> None:
        """scan_inbox skips desktop.ini, Thumbs.db, etc."""
        (mywork / "00_Inbox" / "real_file.pdf").write_bytes(b"real")
        (mywork / "00_Inbox" / "desktop.ini").write_bytes(b"ini")
        (mywork / "00_Inbox" / "Thumbs.db").write_bytes(b"thumbs")

        items = scan_inbox(mywork)
        assert len(items) == 1
        assert items[0].path.name == "real_file.pdf"

    def test_skips_temp_extensions(self, mywork: Path) -> None:
        """scan_inbox skips .tmp, .crdownload, .partial files."""
        (mywork / "00_Inbox" / "download.crdownload").write_bytes(b"partial")
        (mywork / "00_Inbox" / "real.pdf").write_bytes(b"real")

        items = scan_inbox(mywork)
        assert len(items) == 1

    def test_skips_triage_and_manifest_files(self, mywork: Path) -> None:
        """Regression: scan_inbox skips _triage_log.jsonl, _triage_schema.yaml, folder_manifest.yaml."""
        inbox = mywork / "00_Inbox"
        (inbox / "real_file.pdf").write_bytes(b"real")
        (inbox / "_triage_log.jsonl").write_bytes(b"log")
        (inbox / "_triage_schema.yaml").write_bytes(b"schema")
        (inbox / "folder_manifest.yaml").write_bytes(b"manifest")

        items = scan_inbox(mywork)
        assert len(items) == 1
        assert items[0].path.name == "real_file.pdf"

    def test_skips_infrastructure_dirs(self, mywork: Path) -> None:
        """scan_inbox skips _Unmatched, _Staging, .corp directories."""
        (mywork / "00_Inbox" / "real.pdf").write_bytes(b"real")
        unmatched = mywork / "00_Inbox" / "_Unmatched"
        unmatched.mkdir()
        (unmatched / "quarantined.txt").write_bytes(b"q")
        staging = mywork / "00_Inbox" / "_Staging"
        staging.mkdir()
        (staging / "staged.txt").write_bytes(b"s")

        items = scan_inbox(mywork)
        assert len(items) == 1
        assert items[0].path.name == "real.pdf"

    def test_empty_inbox(self, mywork: Path) -> None:
        """scan_inbox returns empty list when inbox has no files."""
        items = scan_inbox(mywork)
        assert items == []

    def test_missing_inbox(self, tmp_path: Path) -> None:
        """scan_inbox returns empty list when 00_Inbox doesn't exist."""
        items = scan_inbox(tmp_path)
        assert items == []


# === compute_file_hash ===


class TestComputeFileHash:
    def test_consistent_hash(self, tmp_path: Path) -> None:
        """Same content produces same hash."""
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        h1 = compute_file_hash(f)
        h2 = compute_file_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """Different content produces different hash."""
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"hello")
        f2.write_bytes(b"world")
        assert compute_file_hash(f1) != compute_file_hash(f2)


# === ingest_file ===


class TestIngestFile:
    def test_series_match_routes_directly(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """File matching a series (high confidence) is routed directly."""
        inbox_file = mywork / "00_Inbox" / "Cognitive_Friday_S12.pptx"
        inbox_file.write_bytes(b"pptx content")

        result = ingest_file(
            inbox_file, mywork, ops, registry, extract=False,
        )
        assert result.action == "routed"
        assert result.match_method == "series"
        assert result.match_series == "cognitive_friday"
        assert result.confidence >= 0.9
        assert result.destination_path == "60_Source_Library/02_Training_Enablement/Cognitive_Friday"
        # File should have been moved
        assert not inbox_file.exists()

    def test_client_match_stages(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Client match (0.80 confidence, >= 0.75 threshold) is routed."""
        inbox_file = mywork / "00_Inbox" / "Lenzing_Notes.docx"
        inbox_file.write_bytes(b"docx content")

        result = ingest_file(
            inbox_file, mywork, ops, registry, extract=False,
        )
        assert result.action == "routed"
        assert result.match_method == "client"
        assert result.confidence == 0.80

    def test_no_match_quarantined(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Unrecognized file is quarantined to _Unmatched."""
        inbox_file = mywork / "00_Inbox" / "random_stuff.txt"
        inbox_file.write_bytes(b"random content")

        result = ingest_file(
            inbox_file, mywork, ops, registry, extract=False,
        )
        assert result.action == "quarantined"
        assert result.match_method == "none"
        assert result.confidence == 0.0
        assert result.destination_path == "00_Inbox/_Unmatched"

    def test_file_not_found(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Non-existent file returns error result."""
        result = ingest_file(
            mywork / "00_Inbox" / "ghost.pdf",
            mywork, ops, registry, extract=False,
        )
        assert result.action == "error"
        assert result.error == "File not found"

    def test_dry_run_no_move(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Dry run matches but doesn't move files or update ops.db."""
        inbox_file = mywork / "00_Inbox" / "Cognitive_Friday_S15.mp4"
        inbox_file.write_bytes(b"mp4 content")

        result = ingest_file(
            inbox_file, mywork, ops, registry,
            extract=False, dry_run=True,
        )
        assert result.action == "routed"
        assert result.match_method == "series"
        # File should NOT have been moved
        assert inbox_file.exists()
        # ops.db should have no assets
        assert ops.get_stats()["total_assets"] == 0

    def test_records_in_ops_db(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Routed file is recorded in ops.db with correct status."""
        inbox_file = mywork / "00_Inbox" / "Cognitive_Friday_S20.pptx"
        inbox_file.write_bytes(b"pptx content")

        ingest_file(inbox_file, mywork, ops, registry, extract=False)

        stats = ops.get_stats()
        assert stats["total_assets"] == 1
        assert stats["total_events"] >= 1

    def test_ops_path_updated_after_move(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Regression: after move, asset path in ops.db reflects the new
        location so subsequent lookups (e.g. extraction) succeed."""
        inbox_file = mywork / "00_Inbox" / "Cognitive_Friday_S25.pptx"
        inbox_file.write_bytes(b"pptx content")

        ingest_file(inbox_file, mywork, ops, registry, extract=False)

        old_path = "00_Inbox/Cognitive_Friday_S25.pptx"
        new_path = "60_Source_Library/02_Training_Enablement/Cognitive_Friday/Cognitive_Friday_S25.pptx"

        # Old path should no longer exist in ops.db
        assert ops.get_asset(old_path) is None
        # New path should exist and have correct status
        asset = ops.get_asset(new_path)
        assert asset is not None
        assert asset["status"] == "routed"
        assert asset["routed_to"] == new_path

    def test_name_collision_handling(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """File with same name at destination gets a suffix."""
        dest = mywork / "00_Inbox" / "_Unmatched"
        dest.mkdir(parents=True)
        (dest / "file.txt").write_bytes(b"existing")

        inbox_file = mywork / "00_Inbox" / "file.txt"
        inbox_file.write_bytes(b"new content")

        result = ingest_file(
            inbox_file, mywork, ops, registry, extract=False,
        )
        assert result.action == "quarantined"
        # Original should still exist, new file moved with suffix
        assert (dest / "file.txt").exists()
        assert (dest / "file_1.txt").exists()


# === ingest_all ===


class TestIngestAll:
    def test_processes_all_inbox_files(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """ingest_all processes every file in 00_Inbox."""
        (mywork / "00_Inbox" / "Cognitive_Friday_S10.pptx").write_bytes(b"a")
        (mywork / "00_Inbox" / "random.txt").write_bytes(b"b")

        file_results, package_results = ingest_all(
            mywork, ops, registry, extract=False,
        )
        assert len(file_results) == 2
        assert len(package_results) == 0
        actions = {r.action for r in file_results}
        assert "routed" in actions
        assert "quarantined" in actions

    def test_processes_folders_and_files(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """ingest_all returns both file and package results."""
        (mywork / "00_Inbox" / "loose.txt").write_bytes(b"loose")
        sub = mywork / "00_Inbox" / "Some_Folder"
        sub.mkdir()
        (sub / "inside.pdf").write_bytes(b"inside")

        file_results, package_results = ingest_all(
            mywork, ops, registry, extract=False,
        )
        assert len(file_results) == 1
        assert len(package_results) == 1
        assert file_results[0].filename == "loose.txt"
        assert package_results[0].folder_name == "Some_Folder"

    def test_empty_inbox_returns_empty(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """ingest_all returns empty lists when inbox is empty."""
        file_results, package_results = ingest_all(
            mywork, ops, registry, extract=False,
        )
        assert file_results == []
        assert package_results == []


# === ingest_folder ===


class TestIngestFolder:
    def test_routes_known_series(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Folder matching Cognitive Friday series routes correctly."""
        sub = mywork / "00_Inbox" / "Cognitive_Friday_S15_Materials"
        sub.mkdir()
        (sub / "slides.pptx").write_bytes(b"slides")
        (sub / "recording.mp4").write_bytes(b"recording")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        assert result.action == "routed"
        assert result.match_method == "series"
        assert result.match_series == "cognitive_friday"
        assert result.confidence >= 0.9
        assert result.file_count == 2
        assert not sub.exists()  # moved

    def test_preserves_structure(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Internal folder structure is preserved after routing."""
        sub = mywork / "00_Inbox" / "Cognitive_Friday_S20_Bundle"
        nested = sub / "extras"
        nested.mkdir(parents=True)
        (sub / "main.pptx").write_bytes(b"main")
        (nested / "bonus.pdf").write_bytes(b"bonus")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        assert result.action == "routed"
        # Check that the internal structure exists at destination
        dest_base = mywork / result.destination_path.replace("/", "\\")
        assert (dest_base / "main.pptx").exists()
        assert (dest_base / "extras" / "bonus.pdf").exists()

    def test_normalizes_name(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Spaces in folder name converted to underscores."""
        sub = mywork / "00_Inbox" / "My  Folder  Name"
        sub.mkdir()
        (sub / "file.txt").write_bytes(b"content")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        # Destination should use underscores, no consecutive underscores
        assert "My_Folder_Name" in result.destination_path
        assert "__" not in result.destination_path

    def test_creates_package(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Package record created in ops.db with correct metadata."""
        sub = mywork / "00_Inbox" / "Lenzing_Workshop"
        sub.mkdir()
        (sub / "deck.pptx").write_bytes(b"deck")
        (sub / "notes.docx").write_bytes(b"notes")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        stats = ops.get_stats()
        assert stats["total_packages"] == 1
        pkg = ops.get_package(1)
        assert pkg is not None
        assert pkg["folder_name"] == "Lenzing_Workshop"
        assert pkg["file_count"] == 2

    def test_registers_files(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """All files inside folder registered as assets linked to package."""
        sub = mywork / "00_Inbox" / "Lenzing_Materials"
        sub.mkdir()
        (sub / "a.pdf").write_bytes(b"a")
        (sub / "b.docx").write_bytes(b"b")

        ingest_folder(sub, mywork, ops, registry, extract=False)

        stats = ops.get_stats()
        assert stats["total_assets"] == 2
        # All assets should be linked to the package
        assets = ops.get_assets_by_status("routed")
        assert len(assets) == 2
        assert all(a["package_id"] == 1 for a in assets)

    def test_quarantines_unknown(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Unknown folder name goes to _Unmatched."""
        sub = mywork / "00_Inbox" / "Random_Stuff"
        sub.mkdir()
        (sub / "file.txt").write_bytes(b"content")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        assert result.action == "quarantined"
        assert "00_Inbox/_Unmatched" in result.destination_path

    def test_dry_run(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Dry run reports without moving."""
        sub = mywork / "00_Inbox" / "Cognitive_Friday_S30"
        sub.mkdir()
        (sub / "file.pptx").write_bytes(b"content")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False, dry_run=True,
        )
        assert result.action == "routed"
        assert sub.exists()  # NOT moved
        assert ops.get_stats()["total_packages"] == 0

    def test_empty_folder_error(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Empty folder returns error result."""
        sub = mywork / "00_Inbox" / "Empty_Folder"
        sub.mkdir()

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        assert result.action == "error"
        assert "Empty folder" in result.error

    def test_large_folder_warning(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry, caplog,
    ) -> None:
        """Folder with depth > 3 logs warning but still proceeds."""
        import logging

        sub = mywork / "00_Inbox" / "Deep_Folder"
        deep = sub / "l1" / "l2" / "l3" / "l4"
        deep.mkdir(parents=True)
        (deep / "file.txt").write_bytes(b"deep")

        with caplog.at_level(logging.WARNING):
            result = ingest_folder(
                sub, mywork, ops, registry, extract=False,
            )

        assert result.action == "quarantined"  # unknown name
        assert "Large folder" in caplog.text

    def test_collision_adds_timestamp(
        self, mywork: Path, ops: OpsDB, registry: ContentRegistry,
    ) -> None:
        """Destination folder already exists → adds timestamp suffix."""
        # Pre-create destination
        dest = mywork / "00_Inbox" / "_Unmatched" / "Existing_Folder"
        dest.mkdir(parents=True)
        (dest / "existing.txt").write_bytes(b"existing")

        sub = mywork / "00_Inbox" / "Existing_Folder"
        sub.mkdir()
        (sub / "new.txt").write_bytes(b"new")

        result = ingest_folder(
            sub, mywork, ops, registry, extract=False,
        )
        assert result.action == "quarantined"
        # Should have a timestamp suffix in the destination
        assert "Existing_Folder_" in result.destination_path
        # Original destination still exists
        assert dest.exists()


# === get_staged_files / finalize_file ===


class TestStagingAndFinalize:
    def test_get_staged_files(self, mywork: Path) -> None:
        """get_staged_files finds files in _Staging directories."""
        staging = mywork / "60_Source_Library" / "02_Training_Enablement" / "_Staging"
        staging.mkdir(parents=True)
        (staging / "review_me.pptx").write_bytes(b"staged content")

        staged = get_staged_files(mywork)
        assert len(staged) == 1
        assert staged[0]["filename"] == "review_me.pptx"
        assert "60_Source_Library/02_Training_Enablement" in staged[0]["parent_destination"]

    def test_finalize_moves_to_parent(self, mywork: Path, ops: OpsDB) -> None:
        """finalize_file moves file from _Staging to parent."""
        dest = mywork / "50_RFP" / "_databases"
        staging = dest / "_Staging"
        staging.mkdir(parents=True)
        staged_file = staging / "data.xlsx"
        staged_file.write_bytes(b"data content")

        ok = finalize_file(staged_file, mywork, ops)
        assert ok is True
        assert not staged_file.exists()
        assert (dest / "data.xlsx").exists()

    def test_finalize_nonexistent(self, mywork: Path, ops: OpsDB) -> None:
        """finalize_file returns False for missing file."""
        ok = finalize_file(mywork / "ghost.txt", mywork, ops)
        assert ok is False

    def test_finalize_not_in_staging(self, mywork: Path, ops: OpsDB) -> None:
        """finalize_file returns False if file is not in _Staging dir."""
        f = mywork / "00_Inbox" / "file.txt"
        f.write_bytes(b"not staged")
        ok = finalize_file(f, mywork, ops)
        assert ok is False
