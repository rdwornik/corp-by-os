"""Tests for manifest_emitter.py — CKE manifest generation."""

from __future__ import annotations

import json

import pytest

from corp_by_os.extraction.non_project.manifest_emitter import (
    build_manifest,
    write_manifest,
    _make_entry_id,
)
from corp_by_os.extraction.non_project.routing import RouteInfo
from corp_by_os.extraction.non_project.folder_policy import ExtractionPolicy
from corp_by_os.extraction.non_project.scanner import ScanResult, scan_folder


@pytest.fixture()
def route_info():
    return RouteInfo(
        vault_target="04_evergreen/_generated/template",
        provenance_scope="template",
        content_origin="mywork",
        source_category="presentation",
        routing_confidence=1.0,
    )


@pytest.fixture()
def policy():
    return ExtractionPolicy(
        enabled=True,
        scope="template",
        allow_extensions=[".pptx", ".pdf"],
        privacy="internal",
    )


def test_build_manifest_structure(mywork_tree, route_info, policy):
    """Generated manifest matches CKE expected format."""
    decks = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    scans = scan_folder(decks, allow_extensions=[".pptx"])
    output = mywork_tree / "output"

    manifest = build_manifest(scans, route_info, policy, output, mywork_root=mywork_tree)

    assert manifest["schema_version"] == 1
    assert "files" in manifest
    assert "output_dir" in manifest
    assert len(manifest["files"]) == 2

    for entry in manifest["files"]:
        assert "id" in entry
        assert "path" in entry
        assert "doc_type" in entry
        assert entry["doc_type"] == "presentation"


def test_build_manifest_v21_fields(mywork_tree, route_info, policy):
    """All v2.1 provenance fields present in each entry."""
    decks = mywork_tree / "30_Templates" / "01_Presentation_Decks"
    scans = scan_folder(decks, allow_extensions=[".pptx"])
    output = mywork_tree / "output"

    manifest = build_manifest(scans, route_info, policy, output, mywork_root=mywork_tree)

    for entry in manifest["files"]:
        assert entry["content_origin"] == "mywork"
        assert entry["source_category"] == "presentation"
        assert "source_locator" in entry
        assert "\\" not in entry["source_locator"]
        assert entry["routing_confidence"] == 1.0


def test_build_manifest_config_section(mywork_tree, route_info, policy):
    """Manifest config contains provenance and policy metadata."""
    scans = scan_folder(
        mywork_tree / "30_Templates" / "01_Presentation_Decks",
        allow_extensions=[".pptx"],
    )
    manifest = build_manifest(scans, route_info, policy, mywork_tree / "out")
    cfg = manifest["config"]
    assert cfg["provenance_scope"] == "template"
    assert cfg["privacy"] == "internal"
    assert cfg["credential_scrubbing"] is False


def test_write_manifest_creates_file(tmp_path, route_info, policy):
    """write_manifest writes valid JSON to disk."""
    scan = ScanResult(
        absolute_path=tmp_path / "test.pdf",
        relative_path="test.pdf",
        extension=".pdf",
        size_bytes=100,
    )
    (tmp_path / "test.pdf").write_bytes(b"fake")

    manifest = build_manifest([scan], route_info, policy, tmp_path / "output")
    out_path = tmp_path / "manifest.json"
    write_manifest(manifest, out_path)

    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == 1
    assert len(loaded["files"]) == 1


def test_make_entry_id_slug():
    """Entry IDs are filesystem-safe slugs with extension."""
    assert (
        _make_entry_id("01_Presentation_Decks/Platform Overview.pptx")
        == "01_presentation_decks--platform-overview--pptx"
    )
    assert _make_entry_id("simple.pdf") == "simple--pdf"


def test_make_entry_id_different_extensions():
    """Same filename with different extensions produces unique IDs."""
    id_docx = _make_entry_id("Know Your Platform/Episode 1.docx")
    id_pdf = _make_entry_id("Know Your Platform/Episode 1.pdf")
    assert id_docx != id_pdf
    assert id_docx.endswith("--docx")
    assert id_pdf.endswith("--pdf")
