"""Tests for overnight duplicate detection."""

from __future__ import annotations

import pytest

from corp_by_os.overnight.dedup import deduplicate, _select_canonical, DuplicateGroup


def _make_scan_result(
    path: str = "file.pptx",
    file_hash: str | None = "sha256:abc123",
    title: str | None = "My Presentation",
    text_preview: str = "Some text content here",
    size_bytes: int = 1024,
    extension: str = ".pptx",
) -> dict:
    meta: dict = {}
    if title:
        meta["title"] = title
    if text_preview:
        meta["text_preview"] = text_preview
    return {
        "path": path,
        "filename": path.split("/")[-1],
        "extension": extension,
        "size_bytes": size_bytes,
        "file_hash": file_hash,
        "tier": 1,
        "metadata": meta,
    }


class TestExactHashDedup:
    def test_exact_hash_dedup(self) -> None:
        files = [
            _make_scan_result("a/file.pptx", file_hash="sha256:aaa", size_bytes=2000),
            _make_scan_result("b/file.pptx", file_hash="sha256:aaa", size_bytes=1000),
            _make_scan_result("c/other.pdf", file_hash="sha256:bbb"),
        ]
        unique, groups = deduplicate(files)
        assert len(unique) == 2  # one from each hash
        assert len(groups) >= 1
        exact = [g for g in groups if g.match_type == "exact_hash"]
        assert len(exact) == 1
        assert len(exact[0].duplicates) == 1

    def test_no_duplicates(self) -> None:
        files = [
            _make_scan_result("a.pptx", file_hash="sha256:aaa"),
            _make_scan_result("b.pdf", file_hash="sha256:bbb"),
            _make_scan_result("c.docx", file_hash="sha256:ccc"),
        ]
        unique, groups = deduplicate(files)
        assert len(unique) == 3
        exact = [g for g in groups if g.match_type == "exact_hash"]
        assert len(exact) == 0

    def test_unhashed_files_kept(self) -> None:
        """Files without hashes should always be kept as unique."""
        files = [
            _make_scan_result("big_video.mp4", file_hash=None, size_bytes=600_000_000),
            _make_scan_result("other.pptx", file_hash="sha256:aaa"),
        ]
        unique, groups = deduplicate(files)
        assert len(unique) == 2


class TestNearDuplicateDetection:
    def test_near_duplicate_title(self) -> None:
        files = [
            _make_scan_result(
                "folder_a/Platform Overview v2.pptx",
                file_hash="sha256:111",
                title="Platform Architecture Overview",
                text_preview="Blue Yonder platform services architecture and deployment",
            ),
            _make_scan_result(
                "folder_b/Platform Overview.pptx",
                file_hash="sha256:222",
                title="Platform Architecture Overview",
                text_preview="Blue Yonder platform services architecture and deployment guide",
            ),
        ]
        unique, groups = deduplicate(files, near_title_threshold=0.90)
        near = [g for g in groups if g.match_type == "near_duplicate"]
        assert len(near) == 1
        assert near[0].similarity >= 0.90

    def test_different_titles_not_flagged(self) -> None:
        files = [
            _make_scan_result("a.pptx", file_hash="sha256:111", title="WMS Training Day 1"),
            _make_scan_result(
                "b.pptx", file_hash="sha256:222", title="Demand Planning Architecture"
            ),
        ]
        unique, groups = deduplicate(files)
        near = [g for g in groups if g.match_type == "near_duplicate"]
        assert len(near) == 0


class TestSelectCanonical:
    def test_select_canonical_largest(self) -> None:
        group = [
            _make_scan_result("a.pptx", size_bytes=1000),
            _make_scan_result("b.pptx", size_bytes=5000),
        ]
        best = _select_canonical(group)
        assert best["size_bytes"] == 5000

    def test_select_canonical_prefers_pptx(self) -> None:
        group = [
            _make_scan_result("doc.pdf", size_bytes=2000, extension=".pdf"),
            _make_scan_result("doc.pptx", size_bytes=2000, extension=".pptx"),
        ]
        best = _select_canonical(group)
        assert best["extension"] == ".pptx"


class TestCrossFormatNotExact:
    def test_cross_format_not_exact_match(self) -> None:
        """Same content in different formats should NOT be exact match."""
        files = [
            _make_scan_result("doc.pptx", file_hash="sha256:pptx_hash"),
            _make_scan_result("doc.pdf", file_hash="sha256:pdf_hash"),
        ]
        unique, groups = deduplicate(files)
        exact = [g for g in groups if g.match_type == "exact_hash"]
        assert len(exact) == 0
        assert len(unique) == 2


class TestDuplicateGroup:
    def test_wasted_bytes(self) -> None:
        g = DuplicateGroup(
            canonical=_make_scan_result("a.pptx", size_bytes=5000),
            duplicates=[
                _make_scan_result("b.pptx", size_bytes=5000),
                _make_scan_result("c.pptx", size_bytes=5000),
            ],
            match_type="exact_hash",
        )
        assert g.total_wasted_bytes == 10000
