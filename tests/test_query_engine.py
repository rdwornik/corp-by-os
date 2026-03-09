"""Tests for query_engine module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corp_by_os.index_builder import rebuild_index
from corp_by_os.query_engine import (
    _sanitize_fts_query,
    get_analytics,
    search_facts,
    search_projects,
)


# --- Fixtures ---


@pytest.fixture()
def populated_index(app_config, tmp_vault: Path, tmp_projects: Path, tmp_path: Path) -> Path:
    """Build an index with test data from multiple projects."""
    db_path = tmp_path / "appdata" / "index.db"

    # Lenzing: has facts
    lenzing = tmp_projects / "Lenzing_Planning"
    knowledge = lenzing / "_knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)

    (knowledge / "project-info.yaml").write_text(yaml.dump({
        "project": "Lenzing_Planning",
        "status": "active",
        "products": ["Planning", "Network"],
        "topics": ["Demand Planning", "SAP Integration", "Security"],
        "people": ["Jan Kowalski"],
    }), encoding="utf-8")

    (knowledge / "facts.yaml").write_text(yaml.dump({
        "project": "Lenzing_Planning",
        "total_facts": 5,
        "facts": [
            {"fact": "SAP integration requires ECC 6.0 or S/4HANA.", "source_title": "Tech Review", "topics": ["SAP Integration"]},
            {"fact": "Demand planning uses weekly buckets for 18 months.", "source_title": "Requirements", "topics": ["Demand Planning"]},
            {"fact": "Security review passed SOC2 Type II.", "source_title": "Security Audit", "topics": ["Security"]},
            {"fact": "Network optimization reduced costs by 12%.", "source_title": "Results", "topics": ["Network"]},
            {"fact": "SAP middleware handles 500K transactions daily.", "source_title": "Architecture", "topics": ["SAP Integration"]},
        ],
    }, default_flow_style=False), encoding="utf-8")

    # Honda: has different products
    honda = tmp_projects / "Honda_Planning"
    honda_knowledge = honda / "_knowledge"
    honda_knowledge.mkdir(parents=True, exist_ok=True)

    (honda_knowledge / "project-info.yaml").write_text(yaml.dump({
        "project": "Honda_Planning",
        "status": "active",
        "products": ["Planning", "WMS"],
        "topics": ["Demand Planning", "WMS Migration"],
    }), encoding="utf-8")

    (honda_knowledge / "facts.yaml").write_text(yaml.dump({
        "project": "Honda_Planning",
        "total_facts": 2,
        "facts": [
            {"fact": "WMS migration from legacy SAP EWM.", "source_title": "Migration Plan", "topics": ["WMS Migration", "SAP Integration"]},
            {"fact": "Demand planning integrates with SAP APO.", "source_title": "Integration Spec", "topics": ["Demand Planning"]},
        ],
    }, default_flow_style=False), encoding="utf-8")

    # Zabka: CatMan product, different domain
    zabka = tmp_projects / "Zabka_CatMan"
    zabka_knowledge = zabka / "_knowledge"
    zabka_knowledge.mkdir(parents=True, exist_ok=True)

    (zabka_knowledge / "project-info.yaml").write_text(yaml.dump({
        "project": "Zabka_CatMan",
        "status": "won",
        "products": ["CatMan"],
        "topics": ["Category Management", "Retail"],
    }), encoding="utf-8")

    rebuild_index(db_path)
    return db_path


# --- Test: Search Facts ---


class TestSearchFacts:
    def test_basic_search(self, populated_index: Path) -> None:
        results = search_facts("SAP", db_path=populated_index)
        assert len(results) >= 2  # Lenzing + Honda both mention SAP

    def test_search_with_project_filter(self, populated_index: Path) -> None:
        results = search_facts("SAP", project_filter="lenzing_planning", db_path=populated_index)
        assert all(r.project_id == "lenzing_planning" for r in results)
        assert len(results) >= 1

    def test_no_results(self, populated_index: Path) -> None:
        results = search_facts("blockchain quantum", db_path=populated_index)
        assert results == []

    def test_returns_client_name(self, populated_index: Path) -> None:
        results = search_facts("demand", db_path=populated_index)
        clients = {r.client for r in results}
        assert "Lenzing" in clients or "Honda" in clients

    def test_returns_source_title(self, populated_index: Path) -> None:
        results = search_facts("SOC2", db_path=populated_index)
        assert len(results) >= 1
        assert results[0].source_title  # should have source_title

    def test_limit_respected(self, populated_index: Path) -> None:
        results = search_facts("SAP", limit=1, db_path=populated_index)
        assert len(results) <= 1

    def test_multi_word_search(self, populated_index: Path) -> None:
        results = search_facts("demand planning", db_path=populated_index)
        assert len(results) >= 1


# --- Test: Search Projects ---


class TestSearchProjects:
    def test_filter_by_product(self, populated_index: Path) -> None:
        results = search_projects(products=["WMS"], db_path=populated_index)
        assert len(results) >= 1
        assert any(r.project_id == "honda_planning" for r in results)

    def test_filter_by_topic(self, populated_index: Path) -> None:
        # "Demand Planning" is in both OneDrive and vault project-info
        results = search_projects(topics=["Demand Planning"], db_path=populated_index)
        assert len(results) >= 1
        assert any(r.project_id == "lenzing_planning" for r in results)

    def test_filter_by_status(self, populated_index: Path) -> None:
        results = search_projects(status="won", db_path=populated_index)
        assert len(results) >= 1
        assert all(r.status == "won" for r in results)

    def test_no_filters_returns_all(self, populated_index: Path) -> None:
        results = search_projects(db_path=populated_index)
        assert len(results) >= 5  # all tmp_projects

    def test_product_not_found(self, populated_index: Path) -> None:
        results = search_projects(products=["QuantumComputing"], db_path=populated_index)
        assert results == []


# --- Test: Analytics ---


class TestAnalytics:
    def test_total_counts(self, populated_index: Path) -> None:
        report = get_analytics(populated_index)
        assert report.total_projects >= 5
        assert report.total_facts == 7  # 5 Lenzing + 2 Honda

    def test_top_topics(self, populated_index: Path) -> None:
        report = get_analytics(populated_index)
        topic_names = [t for t, _ in report.top_topics]
        assert "SAP Integration" in topic_names or "Demand Planning" in topic_names

    def test_top_products(self, populated_index: Path) -> None:
        report = get_analytics(populated_index)
        product_names = [p for p, _ in report.top_products]
        assert "Planning" in product_names

    def test_product_bundles(self, populated_index: Path) -> None:
        report = get_analytics(populated_index)
        # Planning + Network (Lenzing), Planning + WMS (Honda)
        assert isinstance(report.product_bundles, list)

    def test_projects_by_status(self, populated_index: Path) -> None:
        report = get_analytics(populated_index)
        assert "active" in report.projects_by_status or "unknown" in report.projects_by_status

    def test_avg_facts(self, populated_index: Path) -> None:
        report = get_analytics(populated_index)
        assert report.avg_facts_per_project > 0


# --- Test: Helpers ---


class TestHelpers:
    def test_sanitize_fts_basic(self) -> None:
        assert _sanitize_fts_query("SAP integration") == '"SAP" "integration"'

    def test_sanitize_fts_empty(self) -> None:
        assert _sanitize_fts_query("") == ""

    def test_sanitize_fts_single_word(self) -> None:
        assert _sanitize_fts_query("demand") == '"demand"'
