"""Tests for contract.py — manifest validation."""

from __future__ import annotations

import json

import pytest

from corp_by_os.extraction.non_project.contract import (
    ManifestValidationError,
    validate_manifest,
)


def _write_manifest(path, data):
    """Helper to write a manifest JSON file."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def valid_manifest_data(tmp_path):
    """A valid manifest dict with one file entry."""
    test_file = tmp_path / "test.pdf"
    test_file.write_bytes(b"fake-pdf")
    return {
        "schema_version": 1,
        "project": "test",
        "output_dir": str(tmp_path / "output"),
        "files": [
            {
                "id": "test-001",
                "path": str(test_file),
                "doc_type": "document",
                "name": "Test Document",
                "content_origin": "mywork",
                "source_category": "template",
                "source_locator": "30_Templates/test.pdf",
                "routing_confidence": 1.0,
            }
        ],
    }


def test_validate_valid_manifest(tmp_path, valid_manifest_data):
    """Valid manifest passes contract validation."""
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    assert validate_manifest(manifest_path) is True


def test_validate_missing_field(tmp_path, valid_manifest_data):
    """Manifest missing required field raises ManifestValidationError."""
    del valid_manifest_data["files"][0]["id"]
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="missing required field 'id'"):
        validate_manifest(manifest_path)


def test_validate_bad_confidence(tmp_path, valid_manifest_data):
    """routing_confidence outside 0-1 raises error."""
    valid_manifest_data["files"][0]["routing_confidence"] = 1.5
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="routing_confidence"):
        validate_manifest(manifest_path)


def test_validate_bad_schema_version(tmp_path, valid_manifest_data):
    """Wrong schema_version raises error."""
    valid_manifest_data["schema_version"] = 2
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="schema_version must be 1"):
        validate_manifest(manifest_path)


def test_validate_backslash_in_locator(tmp_path, valid_manifest_data):
    """Backslashes in source_locator raise error."""
    valid_manifest_data["files"][0]["source_locator"] = "30_Templates\\test.pdf"
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="backslashes"):
        validate_manifest(manifest_path)


def test_validate_missing_v21_fields(tmp_path, valid_manifest_data):
    """Missing v2.1 provenance fields raises error."""
    del valid_manifest_data["files"][0]["content_origin"]
    del valid_manifest_data["files"][0]["source_category"]
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="missing v2.1 fields"):
        validate_manifest(manifest_path)


def test_validate_duplicate_ids(tmp_path, valid_manifest_data):
    """Duplicate entry IDs raise error."""
    valid_manifest_data["files"].append(valid_manifest_data["files"][0].copy())
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="duplicate id"):
        validate_manifest(manifest_path)


def test_validate_file_not_found(tmp_path, valid_manifest_data):
    """Non-existent file path raises error when check_files_exist=True."""
    valid_manifest_data["files"][0]["path"] = str(tmp_path / "nonexistent.pdf")
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="file not found"):
        validate_manifest(manifest_path, check_files_exist=True)


def test_validate_skip_file_check(tmp_path, valid_manifest_data):
    """Non-existent file path OK when check_files_exist=False."""
    valid_manifest_data["files"][0]["path"] = str(tmp_path / "nonexistent.pdf")
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    assert validate_manifest(manifest_path, check_files_exist=False) is True


def test_validate_invalid_json(tmp_path):
    """Invalid JSON raises ManifestValidationError."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="Invalid JSON"):
        validate_manifest(bad)


def test_validate_unknown_doc_type(tmp_path, valid_manifest_data):
    """Unknown doc_type raises error."""
    valid_manifest_data["files"][0]["doc_type"] = "banana"
    manifest_path = _write_manifest(tmp_path / "manifest.json", valid_manifest_data)
    with pytest.raises(ManifestValidationError, match="unknown doc_type"):
        validate_manifest(manifest_path)
