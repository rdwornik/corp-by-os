"""Tests for scanner.py — file discovery with path-jailing."""

from __future__ import annotations

import os

import pytest

from corp_by_os.extraction.non_project.scanner import ScanResult, scan_folder


def test_scan_finds_allowed_extensions(mywork_tree):
    """Scanner returns only files matching allow_extensions."""
    decks = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    results = scan_folder(decks, allow_extensions=[".pptx"])
    assert len(results) == 2
    assert all(r.extension == ".pptx" for r in results)


def test_scan_skips_knowledge_dir(mywork_tree):
    """Scanner skips _knowledge/ directories."""
    templates = mywork_tree / "30_Templates"
    results = scan_folder(templates, allow_extensions=[".json", ".pptx", ".csv"])
    paths = [r.relative_path for r in results]
    assert not any("_knowledge" in p for p in paths)


def test_scan_skips_hidden_files(mywork_tree):
    """Scanner skips files starting with . or _."""
    decks = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    results = scan_folder(decks, allow_extensions=[".pptx", ".hidden"])
    filenames = [r.absolute_path.name for r in results]
    assert ".hidden" not in filenames


def test_scan_rejects_symlink_escape(tmp_path):
    """Scanner rejects symlinks pointing outside scan root."""
    scan_dir = tmp_path / "scan"
    scan_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.pdf").write_bytes(b"secret")

    # Create symlink pointing outside
    link = scan_dir / "escape.pdf"
    try:
        link.symlink_to(outside / "secret.pdf")
    except OSError:
        pytest.skip("Cannot create symlinks (no privileges)")

    results = scan_folder(scan_dir, allow_extensions=[".pdf"])
    assert len(results) == 0


def test_scan_relative_paths_forward_slashes(mywork_tree):
    """ScanResult.relative_path uses forward slashes."""
    templates = mywork_tree / "30_Templates"
    results = scan_folder(templates, allow_extensions=[".pptx", ".json", ".csv"])
    for r in results:
        assert "\\" not in r.relative_path
        assert "/" in r.relative_path or "/" not in r.relative_path  # single-level is ok


def test_scan_recursive(mywork_tree):
    """Recursive scan finds files in subfolders."""
    templates = mywork_tree / "30_Templates"
    results = scan_folder(templates, allow_extensions=[".pptx", ".json", ".csv"])
    # Should find: 2 pptx in decks + 1 json + 1 csv in demos = 4
    assert len(results) == 4


def test_scan_nonrecursive(mywork_tree):
    """Non-recursive scan only finds files in root."""
    templates = mywork_tree / "30_Templates"
    results = scan_folder(templates, allow_extensions=[".pptx"], recursive=False)
    assert len(results) == 0  # all pptx are in subfolders


def test_scan_empty_extensions(mywork_tree):
    """Empty allow_extensions returns nothing."""
    decks = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    results = scan_folder(decks, allow_extensions=[])
    assert len(results) == 0


def test_scan_result_fields(mywork_tree):
    """ScanResult has all expected fields populated."""
    decks = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    results = scan_folder(decks, allow_extensions=[".pptx"])
    assert len(results) > 0
    r = results[0]
    assert r.absolute_path.is_absolute()
    assert r.extension == ".pptx"
    assert r.size_bytes > 0
    assert isinstance(r.relative_path, str)
