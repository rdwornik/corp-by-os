"""Tests for vault_io module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from corp_by_os.models import VaultZone
from corp_by_os.vault_io import (
    _parse_frontmatter,
    _render_note,
    _write_with_retry,
    copy_to_vault,
    list_projects,
    read_note,
    read_project_info,
    resolve_vault_path,
    validate_vault,
    write_note,
)


class TestFrontmatterParsing:
    def test_parse_with_frontmatter(self):
        content = "---\ntitle: Test\nstatus: active\n---\nBody text here."
        fm, body = _parse_frontmatter(content)
        assert fm["title"] == "Test"
        assert fm["status"] == "active"
        assert body == "Body text here."

    def test_parse_without_frontmatter(self):
        content = "Just plain text."
        fm, body = _parse_frontmatter(content)
        assert fm == {}
        assert body == "Just plain text."

    def test_render_roundtrip(self):
        fm = {"title": "Test", "status": "active"}
        body = "Body text.\n"
        rendered = _render_note(fm, body)
        fm2, body2 = _parse_frontmatter(rendered)
        assert fm2["title"] == "Test"
        assert "Body text." in body2


class TestResolveVaultPath:
    def test_zone_only(self, app_config):
        vp = resolve_vault_path(VaultZone.PROJECTS)
        assert vp.zone == VaultZone.PROJECTS
        assert vp.absolute.name == "01_projects"

    def test_zone_project_file(self, app_config):
        vp = resolve_vault_path(VaultZone.PROJECTS, "lenzing_planning", "project-info.yaml")
        assert "lenzing_planning" in str(vp.absolute)
        assert vp.absolute.name == "project-info.yaml"

    def test_string_zone(self, app_config):
        vp = resolve_vault_path("01_projects", "lenzing_planning")
        assert vp.zone == VaultZone.PROJECTS


class TestReadWriteNote:
    def test_write_read_roundtrip(self, app_config, tmp_vault):
        path = tmp_vault / "01_projects" / "test_project" / "note.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        fm = {"title": "Test Note", "document_type": "meeting"}
        body = "# Meeting Notes\n\nSome content.\n"

        write_note(path, fm, body, mode="create")
        assert path.exists()

        fm2, body2 = read_note(path)
        assert fm2["title"] == "Test Note"
        assert "Meeting Notes" in body2

    def test_upsert_creates(self, app_config, tmp_vault):
        path = tmp_vault / "01_projects" / "new_project" / "note.md"
        write_note(path, {"title": "New"}, "Content\n", mode="upsert")
        assert path.exists()

    def test_upsert_updates(self, app_config, tmp_vault):
        path = tmp_vault / "01_projects" / "lenzing_planning" / "index.md"
        write_note(path, {"title": "Updated"}, "New content\n", mode="upsert")
        fm, body = read_note(path)
        assert fm["title"] == "Updated"

    def test_create_fails_if_exists(self, app_config, tmp_vault):
        path = tmp_vault / "01_projects" / "lenzing_planning" / "index.md"
        with pytest.raises(FileExistsError):
            write_note(path, {"title": "Nope"}, "Body\n", mode="create")

    def test_update_fails_if_missing(self, app_config, tmp_vault):
        path = tmp_vault / "01_projects" / "nonexistent" / "note.md"
        with pytest.raises(FileNotFoundError):
            write_note(path, {"title": "Nope"}, "Body\n", mode="update")


class TestWriteWithRetry:
    def test_succeeds_on_first_try(self, tmp_path):
        path = tmp_path / "test.txt"
        assert _write_with_retry(path, "content")
        assert path.read_text() == "content"

    def test_retries_on_permission_error(self, tmp_path):
        path = tmp_path / "locked.txt"
        call_count = 0

        original_write_text = Path.write_text

        def mock_write(self, content, encoding=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise PermissionError("OneDrive lock")
            return original_write_text(self, content, encoding=encoding)

        with patch.object(Path, "write_text", mock_write):
            _write_with_retry(path, "content", max_retries=5)

        assert call_count == 3

    def test_raises_after_max_retries(self, tmp_path):
        path = tmp_path / "always_locked.txt"

        def mock_write(self, content, encoding=None):
            raise PermissionError("OneDrive lock")

        with patch.object(Path, "write_text", mock_write):
            with pytest.raises(PermissionError):
                _write_with_retry(path, "content", max_retries=2)


class TestReadProjectInfo:
    def test_reads_existing(self, app_config):
        info = read_project_info("lenzing_planning")
        assert info is not None
        assert info.client == "Lenzing AG"
        assert info.status == "active"
        assert info.facts_count == 992

    def test_returns_none_for_missing(self, app_config):
        info = read_project_info("nonexistent_project")
        assert info is None


class TestListProjects:
    def test_merges_onedrive_and_vault(self, app_config):
        projects = list_projects()
        assert len(projects) >= 5  # 5 OneDrive + 1 vault (Lenzing overlaps)

        ids = [p.project_id for p in projects]
        assert "lenzing_planning" in ids
        assert "honda_planning" in ids

        # Lenzing should have both
        lenzing = next(p for p in projects if p.project_id == "lenzing_planning")
        assert lenzing.has_onedrive
        assert lenzing.has_vault

    def test_status_filter(self, app_config):
        active = list_projects(status_filter="active")
        assert all(p.status == "active" for p in active)


class TestCopyToVault:
    def test_copies_files(self, app_config, tmp_vault, tmp_path):
        source = tmp_path / "source_files"
        source.mkdir()
        (source / "note1.md").write_text("Note 1")
        (source / "note2.md").write_text("Note 2")

        copied = copy_to_vault(source, VaultZone.SOURCES, "test_project")
        assert len(copied) == 2

        dest = tmp_vault / "02_sources" / "test_project"
        assert (dest / "note1.md").exists()
        assert (dest / "note2.md").exists()

    def test_immutable_skips_existing(self, app_config, tmp_vault, tmp_path):
        # Pre-create a file in the immutable zone
        dest = tmp_vault / "02_sources" / "test_project"
        dest.mkdir(parents=True)
        (dest / "existing.md").write_text("Original content")

        source = tmp_path / "source_files"
        source.mkdir()
        (source / "existing.md").write_text("New content")
        (source / "new_file.md").write_text("Brand new")

        copied = copy_to_vault(source, VaultZone.SOURCES, "test_project")

        # Only new file should be copied
        assert len(copied) == 1
        assert (dest / "existing.md").read_text() == "Original content"


class TestValidateVault:
    def test_validates_project(self, app_config):
        report = validate_vault(project_id="lenzing_planning")
        assert report.notes_checked >= 1

    def test_reports_missing_project(self, app_config):
        report = validate_vault(project_id="nonexistent_project")
        assert not report.is_valid
