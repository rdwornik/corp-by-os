"""Tests for the MyWork audit module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corp_by_os.audit import (
    _build_file_listing,
    _build_project_listing,
    _parse_gemini_json,
    _repair_truncated_json,
    build_report,
    check_vault_coverage,
    scan_mywork,
)


@pytest.fixture()
def mywork_tree(tmp_path: Path) -> Path:
    """Minimal MyWork folder structure for testing."""
    mywork = tmp_path / "MyWork"

    # 00_Inbox
    inbox = mywork / "00_Inbox"
    inbox.mkdir(parents=True)
    (inbox / "random_file.pptx").write_bytes(b"PK\x03\x04fake")
    (inbox / "notes.txt").write_text("Some notes", encoding="utf-8")

    # 10_Projects
    proj = mywork / "10_Projects" / "Lenzing_Planning"
    proj.mkdir(parents=True)
    (proj / "discovery.pptx").write_bytes(b"PK\x03\x04pptx")
    (proj / "proposal.docx").write_bytes(b"PK\x03\x04docx")

    proj2 = mywork / "10_Projects" / "Honda_Planning"
    proj2.mkdir(parents=True)
    (proj2 / "demo.mp4").write_bytes(b"\x00" * 1024)

    # 30_Templates
    templates = mywork / "30_Templates" / "01_Presentations"
    templates.mkdir(parents=True)
    (templates / "Platform Overview.pptx").write_bytes(b"PK\x03\x04tmpl")

    # 60_Source_Library
    source = mywork / "60_Source_Library"
    source.mkdir(parents=True)
    (source / "product_docs.pdf").write_bytes(b"%PDF-1.4fake")

    # 80_Archive (should be skipped)
    archive = mywork / "80_Archive"
    archive.mkdir(parents=True)
    (archive / "old_stuff.pdf").write_bytes(b"archived")

    # 90_System
    system = mywork / "90_System"
    system.mkdir(parents=True)

    return mywork


@pytest.fixture()
def vault_tree(tmp_path: Path) -> Path:
    """Minimal vault with some extracted notes."""
    vault = tmp_path / "vault"
    sources = vault / "02_sources" / "lenzing_planning"
    sources.mkdir(parents=True)
    (sources / "discovery.md").write_text(
        "---\ntitle: Discovery\n---\nContent\n",
        encoding="utf-8",
    )
    (sources / "platform-overview.md").write_text(
        "---\ntitle: Platform Overview\n---\nContent\n",
        encoding="utf-8",
    )

    evergreen = vault / "04_evergreen" / "_generated"
    evergreen.mkdir(parents=True)
    (evergreen / "supply-chain-best-practices.md").write_text(
        "---\ntitle: Best Practices\n---\n",
        encoding="utf-8",
    )

    return vault


class TestScan:
    def test_scans_all_files(self, mywork_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        names = {f["name"] for f in files}
        assert "random_file.pptx" in names
        assert "discovery.pptx" in names
        assert "product_docs.pdf" in names

    def test_skips_archive(self, mywork_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        paths = [f["path"] for f in files]
        assert not any("80_Archive" in p for p in paths)

    def test_file_metadata(self, mywork_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        pptx = next(f for f in files if f["name"] == "random_file.pptx")
        assert pptx["ext"] == ".pptx"
        assert pptx["folder_l1"] == "00_Inbox"
        assert pptx["size_bytes"] > 0
        assert "modified" in pptx

    def test_folder_levels(self, mywork_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        disc = next(f for f in files if f["name"] == "discovery.pptx")
        assert disc["folder_l1"] == "10_Projects"
        assert disc["folder_l2"] == "Lenzing_Planning"

    def test_forward_slashes_in_path(self, mywork_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        for f in files:
            assert "\\" not in f["path"], f"Backslash in path: {f['path']}"


class TestVaultCoverage:
    def test_finds_extracted_files(self, mywork_tree: Path, vault_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        assert coverage["total_vault_notes"] == 3
        assert coverage["extracted_count"] >= 1  # "discovery" should match

    def test_counts_unextracted(self, mywork_tree: Path, vault_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        assert coverage["not_extracted_count"] >= 1

    def test_by_folder_populated(self, mywork_tree: Path, vault_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        assert len(coverage["by_folder"]) >= 1


class TestBuildReport:
    def test_report_structure(self, mywork_tree: Path, vault_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        report = build_report(files, [], coverage)

        assert "scan_date" in report
        assert "total_files" in report
        assert "total_size_gb" in report
        assert "folders" in report
        assert "vault_coverage" in report
        assert "media_inventory" in report
        assert "duplicate_candidates" in report
        assert report["total_files"] == len(files)

    def test_media_inventory(self, mywork_tree: Path, vault_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        report = build_report(files, [], coverage)

        media = report["media_inventory"]
        assert len(media) >= 1
        assert any(m["ext"] == ".mp4" for m in media)

    def test_duplicate_detection(self, mywork_tree: Path, vault_tree: Path) -> None:
        # Add a duplicate filename
        dup = mywork_tree / "60_Source_Library" / "notes.txt"
        dup.write_text("Duplicate", encoding="utf-8")

        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        report = build_report(files, [], coverage)

        dups = report["duplicate_candidates"]
        notes_dup = [d for d in dups if d["name"] == "notes.txt"]
        assert len(notes_dup) == 1
        assert notes_dup[0]["count"] == 2

    def test_report_serializable(self, mywork_tree: Path, vault_tree: Path) -> None:
        files = scan_mywork(mywork_tree)
        coverage = check_vault_coverage(files, vault_tree)
        report = build_report(files, [], coverage)

        # Must be JSON-serializable
        serialized = json.dumps(report, default=str)
        assert len(serialized) > 0


class TestHelpers:
    def test_parse_gemini_json_plain(self) -> None:
        result = _parse_gemini_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_gemini_json_fenced(self) -> None:
        text = '```json\n{"key": "value"}\n```'
        result = _parse_gemini_json(text)
        assert result == {"key": "value"}

    def test_build_file_listing(self) -> None:
        files = [
            {"path": "a/b.pptx", "size_mb": 1.5, "modified": "2026-03-14T10:00:00"},
        ]
        listing = _build_file_listing(files)
        assert "a/b.pptx" in listing
        assert "1.5 MB" in listing

    def test_parse_gemini_json_truncated_string(self) -> None:
        text = '{"summary": "This is a truncated'
        result = _parse_gemini_json(text)
        assert "summary" in result

    def test_parse_gemini_json_truncated_array(self) -> None:
        text = '{"items": ["a", "b"'
        result = _parse_gemini_json(text)
        assert "items" in result

    def test_parse_gemini_json_truncated_nested(self) -> None:
        text = '{"outer": {"inner": [1, 2'
        result = _parse_gemini_json(text)
        assert "outer" in result

    def test_repair_truncated_json_unclosed_string(self) -> None:
        repaired = _repair_truncated_json('{"key": "val')
        parsed = json.loads(repaired)
        assert parsed["key"] == "val"

    def test_repair_truncated_json_unclosed_array(self) -> None:
        repaired = _repair_truncated_json('{"arr": [1, 2')
        parsed = json.loads(repaired)
        assert parsed["arr"] == [1, 2]

    def test_build_project_listing(self) -> None:
        files = [
            {"folder_l2": "Lenzing_Planning", "size_mb": 5.0, "ext": ".pptx"},
            {"folder_l2": "Lenzing_Planning", "size_mb": 3.0, "ext": ".pdf"},
            {"folder_l2": "Honda_Planning", "size_mb": 1.0, "ext": ".docx"},
        ]
        listing = _build_project_listing(files)
        assert "Lenzing_Planning" in listing
        assert "Honda_Planning" in listing
        assert "2 files" in listing  # Lenzing has 2
