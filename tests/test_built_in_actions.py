"""Tests for built_in_actions module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.built_in_actions import (
    _slugify,
    archive_project,
    copy_to_vault_action,
    create_vault_skeleton,
    generate_attention_dashboard,
    generate_project_brief,
    get_action,
    scan_attention,
    scan_inbox,
)
from corp_by_os.config import get_config
from corp_by_os.models import VaultZone


# --- Test: Action Registry ---


class TestActionRegistry:
    def test_registered_actions(self) -> None:
        assert get_action("create_vault_skeleton") is not None
        assert get_action("validate_project") is not None
        assert get_action("copy_to_vault") is not None
        assert get_action("scan_attention") is not None
        assert get_action("generate_attention_dashboard") is not None
        assert get_action("generate_project_brief") is not None
        assert get_action("archive_project") is not None
        assert get_action("scan_inbox") is not None
        assert get_action("add_task") is not None
        assert get_action("list_tasks") is not None

    def test_unknown_action(self) -> None:
        assert get_action("nonexistent") is None


# --- Test: Slugify ---


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Siemens AG") == "siemens_ag"

    def test_hyphens(self) -> None:
        assert _slugify("Blue-Yonder") == "blue_yonder"


# --- Test: Create Vault Skeleton ---


class TestCreateVaultSkeleton:
    def test_creates_project_dir(self, app_config) -> None:
        result = create_vault_skeleton({"client": "TestCo", "product": "WMS"})
        assert result.success is True

        cfg = get_config()
        project_dir = cfg.vault_path / VaultZone.PROJECTS.value / "testco_wms"
        assert project_dir.exists()
        assert (project_dir / "project-info.yaml").exists()
        assert (project_dir / "index.md").exists()

    def test_project_info_content(self, app_config) -> None:
        create_vault_skeleton({"client": "TestCo", "product": "Planning"})

        cfg = get_config()
        info_file = cfg.vault_path / VaultZone.PROJECTS.value / "testco_planning" / "project-info.yaml"
        data = yaml.safe_load(info_file.read_text(encoding="utf-8"))
        assert data["project_id"] == "testco_planning"
        assert data["client"] == "TestCo"
        assert data["status"] == "active"
        assert data["products"] == ["Planning"]

    def test_missing_client(self, app_config) -> None:
        result = create_vault_skeleton({"product": "WMS"})
        assert result.success is False
        assert "client" in result.error.lower()

    def test_idempotent(self, app_config) -> None:
        create_vault_skeleton({"client": "TestCo", "product": "WMS"})
        result = create_vault_skeleton({"client": "TestCo", "product": "WMS"})
        assert result.success is True  # should not fail on re-run

    def test_client_only(self, app_config) -> None:
        result = create_vault_skeleton({"client": "Acme"})
        assert result.success is True

        cfg = get_config()
        assert (cfg.vault_path / VaultZone.PROJECTS.value / "acme").exists()

    def test_folder_matches_com_new_pattern(self, app_config) -> None:
        """Vault project_id must match com new's {client}_{product} pattern (lowercased).

        com new "ZZZ_Test" -p "IntegrationTest_Planning" creates folder
        ZZZ_Test_IntegrationTest_Planning — vault skeleton must use the same
        base name lowercased.
        """
        result = create_vault_skeleton({
            "client": "ZZZ_Test",
            "product": "IntegrationTest_Planning",
        })
        assert result.success is True

        cfg = get_config()
        expected_id = "zzz_test_integrationtest_planning"
        project_dir = cfg.vault_path / VaultZone.PROJECTS.value / expected_id
        assert project_dir.exists()

        info_file = project_dir / "project-info.yaml"
        data = yaml.safe_load(info_file.read_text(encoding="utf-8"))
        assert data["project_id"] == expected_id
        assert data["client"] == "ZZZ_Test"
        assert data["products"] == ["IntegrationTest_Planning"]

    def test_compound_client_name_preserved(self, app_config) -> None:
        """Client names with underscores must not be double-slugified."""
        result = create_vault_skeleton({"client": "Blue_Yonder", "product": "WMS"})
        assert result.success is True

        cfg = get_config()
        # {client}_{product} = Blue_Yonder_WMS -> lowercase = blue_yonder_wms
        assert (cfg.vault_path / VaultZone.PROJECTS.value / "blue_yonder_wms").exists()


# --- Test: Scan Attention ---


class TestScanAttention:
    def test_scan_with_projects(self, app_config, tmp_vault: Path) -> None:
        params: dict[str, str] = {}
        result = scan_attention(params)
        assert result.success is True
        assert "Scanned" in result.output

    def test_finds_issues(self, app_config, tmp_vault: Path, tmp_projects: Path) -> None:
        # tmp_projects has folders without vault presence
        params: dict[str, str] = {}
        result = scan_attention(params)
        assert result.success is True
        # Should find issues for projects without vault info
        assert "_attention_issues" in params


# --- Test: Generate Attention Dashboard ---


class TestGenerateAttentionDashboard:
    def test_writes_dashboard(self, app_config) -> None:
        params = {
            "_attention_issues": yaml.dump([
                {"project": "test", "severity": "HIGH", "issue": "Missing info"},
            ]),
            "_attention_project_count": "5",
        }
        result = generate_attention_dashboard(params)
        assert result.success is True

        cfg = get_config()
        dashboard = cfg.vault_path / VaultZone.DASHBOARDS.value / "attention.md"
        assert dashboard.exists()
        content = dashboard.read_text(encoding="utf-8")
        assert "Attention Dashboard" in content
        assert "HIGH" in content
        assert "Missing info" in content

    def test_no_issues(self, app_config) -> None:
        params = {
            "_attention_issues": "[]",
            "_attention_project_count": "3",
        }
        result = generate_attention_dashboard(params)
        assert result.success is True

        cfg = get_config()
        dashboard = cfg.vault_path / VaultZone.DASHBOARDS.value / "attention.md"
        content = dashboard.read_text(encoding="utf-8")
        assert "healthy" in content.lower()


# --- Test: Generate Project Brief ---


class TestGenerateProjectBrief:
    def test_generates_brief(self, app_config) -> None:
        result = generate_project_brief({"project": "lenzing_planning"})
        assert result.success is True
        assert "brief" in result.output.lower()

        cfg = get_config()
        brief = cfg.vault_path / VaultZone.PROJECTS.value / "lenzing_planning" / "brief.md"
        assert brief.exists()
        content = brief.read_text(encoding="utf-8")
        assert "Lenzing AG" in content
        assert "Project Brief" in content

    def test_missing_project(self, app_config) -> None:
        result = generate_project_brief({"project": "nonexistent_xyz"})
        assert result.success is False
        assert "no project-info.yaml found" in result.error.lower()


# --- Test: Archive Project ---


class TestArchiveProject:
    def test_archive_moves_folder(self, app_config, tmp_projects: Path, tmp_path: Path) -> None:
        # Create archive dir
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()

        # Patch to use tmp archive
        import os
        os.environ["ARCHIVE_ROOT"] = str(archive_dir)
        get_config.cache_clear()

        result = archive_project({
            "project": "Honda_Planning",
            "reason": "won",
            "project_path": str(tmp_projects / "Honda_Planning"),
        })
        assert result.success is True

        # Verify moved
        from datetime import date
        year = str(date.today().year)
        assert (archive_dir / year / "Honda_Planning").exists()
        assert not (tmp_projects / "Honda_Planning").exists()

    def test_archive_missing_project(self, app_config) -> None:
        result = archive_project({"project": "", "reason": "lost"})
        assert result.success is False


# --- Test: Scan Inbox ---


class TestScanInbox:
    def test_scan_nonexistent_inbox(self, app_config) -> None:
        result = scan_inbox({})
        # Should succeed even if inbox doesn't exist
        assert result.success is True

    def test_scan_with_files(self, app_config, tmp_path: Path) -> None:
        # Create a fake inbox
        inbox = get_config().projects_root.parent / "00_Inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "test.pdf").write_text("fake pdf", encoding="utf-8")
        (inbox / "notes.md").write_text("# Notes", encoding="utf-8")

        result = scan_inbox({})
        assert result.success is True
        assert "2 files" in result.output


# --- Test: Copy To Vault ---


class TestCopyToVault:
    def test_copy_knowledge(self, app_config, tmp_projects: Path) -> None:
        # Create _knowledge dir in a project
        project_dir = tmp_projects / "Lenzing_Planning"
        knowledge_dir = project_dir / "_knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "index.md").write_text("# Index", encoding="utf-8")
        (knowledge_dir / "facts.yaml").write_text("- fact: test", encoding="utf-8")

        result = copy_to_vault_action({
            "project": "lenzing_planning",
            "project_path": str(project_dir),
        })
        assert result.success is True
        assert "Copied" in result.output
