"""Tests for folder_policy.py — extraction policy loading."""

from __future__ import annotations

import pytest

from corp_by_os.extraction.non_project.folder_policy import (
    ExtractionPolicy,
    PolicyError,
    load_policy,
)


def test_load_policy_basic(mywork_tree):
    """Loads extraction policy from folder_manifest.yaml."""
    policy = load_policy(mywork_tree / "30_Templates")
    assert policy.enabled is True
    assert policy.scope == "template"
    assert ".pptx" in policy.allow_extensions
    assert policy.privacy == "internal"


def test_load_policy_subfolder_own_manifest(mywork_tree):
    """Subfolder with its own manifest loads credential_scrubbing."""
    policy = load_policy(mywork_tree / "30_Templates" / "02_Demo_Scripts")
    assert policy.enabled is True
    assert policy.credential_scrubbing is True


def test_load_policy_subfolder_inherits_parent(mywork_tree):
    """Subfolder without manifest inherits from parent + checks subfolders section."""
    # 01_Presentation_Decks has no own manifest, falls back to parent
    policy = load_policy(mywork_tree / "30_Templates" / "01_Presentation_Decks")
    assert policy.enabled is True
    assert policy.scope == "template"
    assert policy.credential_scrubbing is False


def test_load_policy_extraction_disabled(mywork_tree):
    """00_Inbox has extraction disabled."""
    policy = load_policy(mywork_tree / "00_Inbox")
    assert policy.enabled is False


def test_load_policy_missing_manifest(tmp_path):
    """Folder with no manifest raises PolicyError."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PolicyError, match="No folder_manifest.yaml found"):
        load_policy(empty)


def test_load_policy_subfolder_credential_scrubbing_from_parent(mywork_tree):
    """Parent's subfolders section sets credential_scrubbing on child without own manifest."""
    # Remove the subfolder's own manifest so it falls back to parent
    own_manifest = mywork_tree / "30_Templates" / "02_Demo_Scripts" / "folder_manifest.yaml"
    own_manifest.unlink()

    policy = load_policy(mywork_tree / "30_Templates" / "02_Demo_Scripts")
    assert policy.credential_scrubbing is True
