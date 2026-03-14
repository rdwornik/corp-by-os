"""Tests for source-tracking freshness scanner."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from corp_by_os.freshness.scanner import (
    REVIEW_AGE_DAYS,
    FreshnessResult,
    FreshnessSummary,
    compute_hash,
    parse_frontmatter,
    scan_note_freshness,
    scan_vault_freshness,
)


def _write_note(
    path: Path,
    source_path: str | None = None,
    source_hash: str | None = None,
    source_mtime: str | None = None,
    extracted_at: str | None = None,
    extra_fm: str = "",
) -> Path:
    """Write a vault note with YAML frontmatter."""
    fm_lines = ["---", "title: Test Note"]
    if source_path is not None:
        fm_lines.append(f"source_path: {source_path}")
    if source_hash is not None:
        fm_lines.append(f"source_hash: {source_hash}")
    if source_mtime is not None:
        fm_lines.append(f"source_mtime: '{source_mtime}'")
    if extracted_at is not None:
        fm_lines.append(f"extracted_at: '{extracted_at}'")
    if extra_fm:
        fm_lines.append(extra_fm)
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append("Note content here.")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(fm_lines), encoding="utf-8")
    return path


def _hash_content(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class TestComputeHash:
    def test_computes_sha256(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_bytes(b"hello world")
        assert compute_hash(f) == _hash_content(b"hello world")

    def test_large_file(self, tmp_path: Path) -> None:
        """Large files are hashed correctly (chunked)."""
        f = tmp_path / "big.bin"
        data = b"x" * 100_000
        f.write_bytes(data)
        assert compute_hash(f) == _hash_content(data)


class TestParseFrontmatter:
    def test_valid_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "note.md"
        f.write_text("---\ntitle: Test\nsource_path: foo.txt\n---\nBody", encoding="utf-8")
        fm = parse_frontmatter(f)
        assert fm is not None
        assert fm["title"] == "Test"
        assert fm["source_path"] == "foo.txt"

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "note.md"
        f.write_text("Just plain text", encoding="utf-8")
        assert parse_frontmatter(f) is None

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        f = tmp_path / "note.md"
        f.write_text("---\n: [invalid yaml\n---\nBody", encoding="utf-8")
        assert parse_frontmatter(f) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.md"
        assert parse_frontmatter(f) is None


class TestScanNoteFreshness:
    def test_fresh_note(self, tmp_path: Path) -> None:
        """Note with matching source mtime -> fresh (lazy, no hash needed)."""
        source = tmp_path / "source.docx"
        source.write_bytes(b"source content")
        mtime = datetime.fromtimestamp(source.stat().st_mtime).isoformat()

        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path=str(source),
            source_hash=_hash_content(b"source content"),
            source_mtime=mtime,
            extracted_at=datetime.now().isoformat(),
        )

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "fresh"
        assert "mtime match" in result.reason

    def test_stale_note(self, tmp_path: Path) -> None:
        """Note where source content changed -> stale."""
        source = tmp_path / "source.docx"
        source.write_bytes(b"original content")
        old_hash = _hash_content(b"original content")

        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path=str(source),
            source_hash=old_hash,
            source_mtime="2025-01-01T00:00:00",  # old mtime forces hash check
            extracted_at=datetime.now().isoformat(),
        )

        # Modify source content
        source.write_bytes(b"modified content")

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "stale"
        assert "hash mismatch" in result.reason
        assert result.source_hash_new == _hash_content(b"modified content")

    def test_orphaned_note(self, tmp_path: Path) -> None:
        """Note where source file is deleted -> orphaned."""
        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path=str(tmp_path / "deleted_file.docx"),
            extracted_at=datetime.now().isoformat(),
        )

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "orphaned"
        assert "missing" in result.reason.lower()

    def test_review_due(self, tmp_path: Path) -> None:
        """Note extracted >180 days ago -> review_due."""
        source = tmp_path / "source.docx"
        source.write_bytes(b"content")
        mtime = datetime.fromtimestamp(source.stat().st_mtime).isoformat()

        old_date = (datetime.now() - timedelta(days=200)).isoformat()
        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path=str(source),
            source_hash=_hash_content(b"content"),
            source_mtime=mtime,
            extracted_at=old_date,
        )

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "review_due"
        assert result.days_since_extraction is not None
        assert result.days_since_extraction > REVIEW_AGE_DAYS

    def test_no_source_path(self, tmp_path: Path) -> None:
        """Legacy v1 note without source_path -> no_source."""
        note = _write_note(
            tmp_path / "vault" / "note.md",
            # no source_path
        )

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "no_source"
        assert "legacy" in result.reason.lower()

    def test_lazy_hash_skip(self, tmp_path: Path) -> None:
        """When mtime unchanged, hash is NOT computed (source_hash_new is None)."""
        source = tmp_path / "source.docx"
        source.write_bytes(b"content")
        mtime = datetime.fromtimestamp(source.stat().st_mtime).isoformat()

        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path=str(source),
            source_hash=_hash_content(b"content"),
            source_mtime=mtime,
            extracted_at=datetime.now().isoformat(),
        )

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "fresh"
        # Key assertion: hash_new is None because mtime matched (lazy skip)
        assert result.source_hash_new is None

    def test_frontmatter_parse_error(self, tmp_path: Path) -> None:
        """Invalid YAML frontmatter -> error status."""
        note = tmp_path / "vault" / "note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("Not a valid note file", encoding="utf-8")

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "error"

    def test_source_path_absolute(self, tmp_path: Path) -> None:
        """Absolute source_path resolves correctly."""
        source = tmp_path / "abs_source.txt"
        source.write_bytes(b"data")
        mtime = datetime.fromtimestamp(source.stat().st_mtime).isoformat()

        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path=str(source),
            source_hash=_hash_content(b"data"),
            source_mtime=mtime,
            extracted_at=datetime.now().isoformat(),
        )

        result = scan_note_freshness(note, tmp_path)
        assert result.status == "fresh"

    def test_source_path_relative(self, tmp_path: Path) -> None:
        """Relative source_path resolves against mywork_root."""
        source = tmp_path / "mywork" / "docs" / "file.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"relative data")
        mtime = datetime.fromtimestamp(source.stat().st_mtime).isoformat()
        mywork = tmp_path / "mywork"

        note = _write_note(
            tmp_path / "vault" / "note.md",
            source_path="docs/file.txt",
            source_hash=_hash_content(b"relative data"),
            source_mtime=mtime,
            extracted_at=datetime.now().isoformat(),
        )

        result = scan_note_freshness(note, mywork)
        assert result.status == "fresh"


class TestScanVaultFreshness:
    def test_scan_vault(self, tmp_path: Path) -> None:
        """Full vault scan returns correct summary counts."""
        vault = tmp_path / "vault"
        mywork = tmp_path / "mywork"
        sources_dir = vault / "02_sources"
        sources_dir.mkdir(parents=True)

        # Fresh note
        source1 = mywork / "doc1.txt"
        source1.parent.mkdir(parents=True, exist_ok=True)
        source1.write_bytes(b"content1")
        mtime1 = datetime.fromtimestamp(source1.stat().st_mtime).isoformat()
        _write_note(
            sources_dir / "fresh.md",
            source_path=str(source1),
            source_hash=_hash_content(b"content1"),
            source_mtime=mtime1,
            extracted_at=datetime.now().isoformat(),
        )

        # Orphaned note
        _write_note(
            sources_dir / "orphaned.md",
            source_path=str(mywork / "deleted.txt"),
            extracted_at=datetime.now().isoformat(),
        )

        # No-source note
        _write_note(sources_dir / "legacy.md")

        summary = scan_vault_freshness(vault, mywork)
        assert summary.total_scanned == 3
        assert summary.fresh == 1
        assert summary.orphaned == 1
        assert summary.no_source == 1

    def test_scan_vault_empty(self, tmp_path: Path) -> None:
        """Empty vault returns zeroed summary."""
        vault = tmp_path / "vault"
        vault.mkdir()
        mywork = tmp_path / "mywork"
        mywork.mkdir()

        summary = scan_vault_freshness(vault, mywork)
        assert summary.total_scanned == 0
        assert summary.results == []

    def test_scan_vault_both_dirs(self, tmp_path: Path) -> None:
        """Scans both 02_sources and 04_evergreen."""
        vault = tmp_path / "vault"
        mywork = tmp_path / "mywork"
        mywork.mkdir()

        for subdir in ("02_sources", "04_evergreen"):
            d = vault / subdir
            d.mkdir(parents=True)
            _write_note(d / "note.md")

        summary = scan_vault_freshness(vault, mywork)
        assert summary.total_scanned == 2
