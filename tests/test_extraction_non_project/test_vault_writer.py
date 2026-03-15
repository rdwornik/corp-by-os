"""Tests for vault_writer.py -- atomic vault write."""

from __future__ import annotations

from pathlib import Path

from corp_by_os.extraction.vault_writer import move_to_vault


def _make_package(staging: Path, pkg_name: str, files: dict[str, bytes]) -> None:
    """Helper to create a fake CKE output package in staging."""
    pkg = staging / pkg_name
    for rel_path, content in files.items():
        f = pkg / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(content)


def test_move_to_vault_creates_dir(tmp_path):
    """Creates vault target directory if it doesn't exist."""
    staging = tmp_path / "staging"
    _make_package(staging, "pkg-001", {"extract/extract.json": b'{"test": 1}'})

    vault = tmp_path / "vault"
    move_to_vault(staging, vault, "04_evergreen/_generated/template")

    assert (vault / "04_evergreen" / "_generated" / "template" / "pkg-001").is_dir()


def test_move_to_vault_moves_files(tmp_path):
    """Files moved from staging to vault target."""
    staging = tmp_path / "staging"
    _make_package(
        staging,
        "doc-001",
        {
            "extract/extract.json": b'{"id": "doc-001"}',
            "extract/readme.md": b"# Doc 001",
        },
    )

    vault = tmp_path / "vault"
    count = move_to_vault(staging, vault, "target")

    dest = vault / "target" / "doc-001"
    assert (dest / "extract" / "extract.json").exists()
    assert (dest / "extract" / "readme.md").exists()
    assert count == 2


def test_move_to_vault_skips_identical(tmp_path):
    """Files with identical hash not overwritten."""
    content = b"identical content"

    # Pre-populate vault with identical file
    vault = tmp_path / "vault"
    dest = vault / "target" / "pkg-001" / "extract"
    dest.mkdir(parents=True)
    (dest / "extract.json").write_bytes(content)

    # Staging has same content
    staging = tmp_path / "staging"
    _make_package(staging, "pkg-001", {"extract/extract.json": content})

    count = move_to_vault(staging, vault, "target")
    assert count == 0  # Nothing moved -- identical


def test_move_to_vault_overwrites_changed(tmp_path):
    """Files with different hash are overwritten."""
    # Pre-populate vault with old content
    vault = tmp_path / "vault"
    dest = vault / "target" / "pkg-001" / "extract"
    dest.mkdir(parents=True)
    (dest / "extract.json").write_bytes(b"old content")

    # Staging has new content
    staging = tmp_path / "staging"
    _make_package(staging, "pkg-001", {"extract/extract.json": b"new content"})

    count = move_to_vault(staging, vault, "target")
    assert count == 1
    assert (dest / "extract.json").read_bytes() == b"new content"


def test_move_to_vault_returns_count(tmp_path):
    """Returns correct count of files moved."""
    staging = tmp_path / "staging"
    _make_package(staging, "pkg-a", {"extract/a.md": b"a"})
    _make_package(staging, "pkg-b", {"extract/b.md": b"b", "extract/b.json": b"{}"})

    vault = tmp_path / "vault"
    count = move_to_vault(staging, vault, "target")
    assert count == 3


def test_move_to_vault_empty_staging(tmp_path):
    """Empty staging dir returns 0."""
    staging = tmp_path / "staging"
    staging.mkdir()
    vault = tmp_path / "vault"
    count = move_to_vault(staging, vault, "target")
    assert count == 0
