"""Tests for template_manager module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.models import TemplateInfo
from corp_by_os.template_manager import (
    _make_id,
    copy_template,
    load_registry,
    save_registry,
    scan_templates,
    select_template,
)


# --- Fixtures ---


@pytest.fixture()
def templates_dir(tmp_path: Path) -> Path:
    """Create a fake 30_Templates/ structure."""
    root = tmp_path / "templates"
    root.mkdir()

    # Root-level files
    (root / "Corporate_Deck.pptx").write_bytes(b"FAKE" * 25000)  # ~100KB
    (root / "Discovery_Questions.xlsx").write_bytes(b"FAKE" * 100)

    # Subdirectories
    demo = root / "Demo Scripts"
    demo.mkdir()
    (demo / "API - BDM Ingestion.docx").write_bytes(b"FAKE" * 50)
    (demo / "payload_data.csv").write_bytes(b"col1,col2\n1,2\n")

    prep = root / "Prepare Presentation"
    prep.mkdir()
    (prep / "Platform Overview.pptx").write_bytes(b"FAKE" * 5000)
    (prep / "Architecture_Integration.pptx").write_bytes(b"FAKE" * 3000)
    (prep / "Snowflake-to-Snowflake.pptx").write_bytes(b"FAKE" * 2000)

    # Skipped directories
    (root / "Deprecated").mkdir()
    (root / "Deprecated" / "Old_Deck.pptx").write_bytes(b"OLD")
    (root / "Working").mkdir()
    (root / "Working" / "Draft.pptx").write_bytes(b"DRAFT")
    (root / "Canonical").mkdir()

    return root


@pytest.fixture()
def sample_registry(tmp_path: Path) -> tuple[Path, list[TemplateInfo]]:
    """Create a sample registry file and return path + data."""
    templates = [
        TemplateInfo(
            id="corporate_deck",
            name="Corporate Deck",
            file="Corporate_Deck.pptx",
            path="30_Templates/Corporate_Deck.pptx",
            size_mb=94.0,
            type="presentation",
            use_cases=["corporate overview", "executive briefing", "first meeting"],
            domains=["Go-to-Market"],
            tags=["corporate", "overview"],
            language="en",
        ),
        TemplateInfo(
            id="discovery_questions",
            name="Discovery Questions",
            file="Discovery_Questions.xlsx",
            path="30_Templates/Discovery_Questions.xlsx",
            size_mb=0.025,
            type="questionnaire",
            use_cases=["discovery call preparation", "qualification"],
            domains=["Go-to-Market"],
            tags=["discovery", "questions", "qualification"],
            language="en",
        ),
        TemplateInfo(
            id="platform_overview",
            name="Platform Overview",
            file="Platform_Overview.pptx",
            path="30_Templates/Prepare Presentation/Platform_Overview.pptx",
            size_mb=5.0,
            type="presentation",
            use_cases=["architecture overview", "technical deep dive"],
            domains=["Product", "Technical"],
            tags=["platform", "architecture", "technical", "presentation"],
            language="en",
        ),
        TemplateInfo(
            id="architecture_integration",
            name="Architecture Integration",
            file="Architecture_Integration.pptx",
            path="30_Templates/Prepare Presentation/Architecture_Integration.pptx",
            size_mb=3.0,
            type="presentation",
            use_cases=["integration", "architecture overview"],
            domains=["Product", "Technical"],
            tags=["architecture", "technical", "integration", "presentation"],
            language="en",
        ),
    ]

    registry_path = tmp_path / "template_registry.yaml"
    data = {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "file": t.file,
                "path": t.path,
                "size_mb": t.size_mb,
                "type": t.type,
                "use_cases": t.use_cases,
                "domains": t.domains,
                "tags": t.tags,
                "language": t.language,
            }
            for t in templates
        ],
    }
    registry_path.write_text(
        yaml.dump(data, default_flow_style=False),
        encoding="utf-8",
    )
    return registry_path, templates


# --- Test: Scan ---


class TestScan:
    def test_finds_all_files(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        # Should find: Corporate_Deck.pptx, Discovery_Questions.xlsx,
        # API - BDM Ingestion.docx, payload_data.csv,
        # Platform Overview.pptx, Architecture_Integration.pptx,
        # Snowflake-to-Snowflake.pptx
        assert len(templates) == 7

    def test_skips_deprecated(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        ids = [t.id for t in templates]
        assert "old_deck" not in ids

    def test_skips_working(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        ids = [t.id for t in templates]
        assert "draft" not in ids

    def test_infers_type(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        by_id = {t.id: t for t in templates}
        assert by_id["corporate_deck"].type == "presentation"
        assert by_id["discovery_questions"].type == "questionnaire"
        assert by_id["payload_data"].type == "data"

    def test_demo_script_type(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        by_id = {t.id: t for t in templates}
        assert by_id["api_bdm_ingestion"].type == "demo_script"

    def test_relative_path(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        for t in templates:
            assert t.path.startswith("30_Templates/")

    def test_auto_tags(self, templates_dir: Path) -> None:
        templates = scan_templates(templates_dir)
        by_id = {t.id: t for t in templates}
        corp = by_id["corporate_deck"]
        assert "corporate" in corp.tags
        assert "overview" in corp.tags

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        templates = scan_templates(tmp_path / "nonexistent")
        assert templates == []

    def test_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_templates"
        empty.mkdir()
        templates = scan_templates(empty)
        assert templates == []

    def test_skips_office_temp_files(self, templates_dir: Path) -> None:
        (templates_dir / "~$Corporate_Deck.pptx").write_bytes(b"TEMP")
        templates = scan_templates(templates_dir)
        ids = [t.id for t in templates]
        assert not any(i.startswith("_") for i in ids)


# --- Test: Registry load/save ---


class TestRegistry:
    def test_roundtrip(self, tmp_path: Path) -> None:
        templates = [
            TemplateInfo(
                id="test",
                name="Test Template",
                file="test.pptx",
                path="30_Templates/test.pptx",
                size_mb=1.0,
                type="presentation",
                use_cases=["demo"],
                domains=["Technical"],
                tags=["test"],
            ),
        ]
        path = save_registry(templates, tmp_path / "reg.yaml")
        loaded = load_registry(path)
        assert len(loaded) == 1
        assert loaded[0].id == "test"
        assert loaded[0].name == "Test Template"
        assert loaded[0].tags == ["test"]

    def test_load_from_sample(self, sample_registry) -> None:
        path, expected = sample_registry
        loaded = load_registry(path)
        assert len(loaded) == len(expected)
        assert loaded[0].id == expected[0].id

    def test_load_missing_file(self, tmp_path: Path) -> None:
        loaded = load_registry(tmp_path / "nonexistent.yaml")
        assert loaded == []

    def test_load_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        loaded = load_registry(empty)
        assert loaded == []


# --- Test: Selection ---


class TestSelect:
    def test_discovery_match(self, sample_registry) -> None:
        _, templates = sample_registry
        selected = select_template("discovery call", templates)
        assert selected is not None
        assert selected.id == "discovery_questions"

    def test_architecture_match(self, sample_registry) -> None:
        _, templates = sample_registry
        selected = select_template("architecture overview", templates)
        assert selected is not None
        assert selected.id in ("platform_overview", "architecture_integration")

    def test_integration_match(self, sample_registry) -> None:
        _, templates = sample_registry
        selected = select_template("integration demo", templates)
        assert selected is not None
        assert "integration" in selected.tags or "integration" in " ".join(selected.use_cases)

    def test_corporate_overview(self, sample_registry) -> None:
        _, templates = sample_registry
        selected = select_template("corporate overview for executive", templates)
        assert selected is not None
        assert selected.id == "corporate_deck"

    def test_fallback_to_largest_presentation(self, sample_registry) -> None:
        _, templates = sample_registry
        # Gibberish goal should fallback to largest presentation
        selected = select_template("xyzzy nothing matches", templates)
        assert selected is not None
        assert selected.type == "presentation"
        assert selected.size_mb == 94.0  # the corporate deck

    def test_empty_templates(self) -> None:
        selected = select_template("anything", [])
        assert selected is None

    def test_technical_deep_dive(self, sample_registry) -> None:
        _, templates = sample_registry
        selected = select_template("technical deep dive on platform", templates)
        assert selected is not None
        assert "technical" in selected.tags


# --- Test: Copy ---


class TestCopy:
    def test_copy_template(
        self, templates_dir: Path, tmp_path: Path, app_config, monkeypatch
    ) -> None:
        monkeypatch.setenv("TEMPLATES_ROOT", str(templates_dir))
        from corp_by_os.config import get_config

        get_config.cache_clear()

        template = TemplateInfo(
            id="corporate_deck",
            name="Corporate Deck",
            file="Corporate_Deck.pptx",
            path="30_Templates/Corporate_Deck.pptx",
            size_mb=94.0,
            type="presentation",
        )

        dest = tmp_path / "output"
        result = copy_template(template, dest, "Lenzing_2026-03-09_Demo.pptx")
        assert result.exists()
        assert result.name == "Lenzing_2026-03-09_Demo.pptx"
        assert result.stat().st_size > 0

    def test_copy_missing_source(self, tmp_path: Path, app_config, monkeypatch) -> None:
        monkeypatch.setenv("TEMPLATES_ROOT", str(tmp_path / "empty"))
        from corp_by_os.config import get_config

        get_config.cache_clear()

        template = TemplateInfo(
            id="missing",
            name="Missing",
            file="Nonexistent.pptx",
            path="30_Templates/Nonexistent.pptx",
            size_mb=0,
            type="presentation",
        )

        with pytest.raises(FileNotFoundError):
            copy_template(template, tmp_path / "output", "test.pptx")


# --- Test: ID generation ---


class TestMakeId:
    def test_simple(self) -> None:
        assert _make_id("Corporate_Deck.pptx") == "corporate_deck"

    def test_spaces_and_hyphens(self) -> None:
        assert _make_id("API - BDM Ingestion.docx") == "api_bdm_ingestion"

    def test_apostrophe(self) -> None:
        result = _make_id("Enterprise Data Orchestration Pitch Deck '25.pptx")
        assert "enterprise" in result
        assert "25" in result
