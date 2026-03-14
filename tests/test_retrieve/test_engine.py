"""Tests for retrieval engine."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from corp_by_os.retrieve.engine import (
    RetrievalFilter,
    RetrievalResult,
    RetrievedNote,
    _build_fts_query,
    _find_coverage_gaps,
    _load_note_content,
    _parse_json_field,
    retrieve,
)


# --- Schema for test index.db ---

_TEST_SCHEMA = """\
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    client TEXT,
    title TEXT NOT NULL,
    type TEXT,
    source_type TEXT,
    layer TEXT,
    source TEXT,
    topics TEXT,
    products TEXT,
    domains TEXT,
    people TEXT,
    confidentiality TEXT,
    quality TEXT,
    language TEXT,
    date TEXT,
    valid_to TEXT,
    model TEXT,
    tokens_used INTEGER,
    content_origin TEXT,
    source_category TEXT,
    source_locator TEXT,
    routing_confidence REAL,
    note_path TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, topics, products, domains, client, project_id,
    content=notes, content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, topics, products, domains, client, project_id)
    VALUES (new.id, new.title, new.topics, new.products, new.domains, new.client, new.project_id);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, topics, products, domains, client, project_id)
    VALUES ('delete', old.id, old.title, old.topics, old.products, old.domains, old.client, old.project_id);
END;
"""


@pytest.fixture()
def test_db(tmp_path: Path) -> Path:
    """Create a test index.db with sample notes."""
    db_path = tmp_path / "index.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_TEST_SCHEMA)

    # Create vault notes on disk
    vault = tmp_path / "vault"
    vault.mkdir()

    notes = [
        {
            "project_id": "lenzing_planning",
            "client": "Lenzing",
            "title": "Lenzing Discovery Workshop Notes",
            "type": "meeting_notes",
            "source_type": "meeting",
            "topics": json.dumps(["Demand Planning", "Forecasting"]),
            "products": json.dumps(["Cognitive Demand Planning"]),
            "domains": json.dumps(["Planning"]),
            "note_path": str(vault / "lenzing_discovery.md"),
        },
        {
            "project_id": "lenzing_planning",
            "client": "Lenzing",
            "title": "Lenzing RFP Response Architecture",
            "type": "rfp",
            "source_type": "documentation",
            "topics": json.dumps(["Architecture", "Integration"]),
            "products": json.dumps(["Cognitive Demand Planning", "Platform"]),
            "domains": json.dumps(["Planning", "Platform"]),
            "note_path": str(vault / "lenzing_rfp.md"),
        },
        {
            "project_id": "sgdbf_retail",
            "client": "SGDBF",
            "title": "SGDBF WMS Implementation Requirements",
            "type": "requirements",
            "source_type": "documentation",
            "topics": json.dumps(["WMS", "Warehouse Operations"]),
            "products": json.dumps(["WMS"]),
            "domains": json.dumps(["Warehousing"]),
            "note_path": str(vault / "sgdbf_wms.md"),
        },
        {
            "project_id": "general",
            "client": None,
            "title": "Platform Architecture Overview",
            "type": "training",
            "source_type": "training",
            "topics": json.dumps(["Architecture", "Platform"]),
            "products": json.dumps(["Platform"]),
            "domains": json.dumps(["Platform"]),
            "note_path": str(vault / "platform_arch.md"),
        },
        {
            "project_id": "lenzing_planning",
            "client": "Lenzing",
            "title": "Lenzing Competitive Analysis",
            "type": "competitive",
            "source_type": "competitive",
            "topics": json.dumps(["Competitive", "SAP IBP"]),
            "products": json.dumps(["Cognitive Demand Planning"]),
            "domains": json.dumps(["Planning"]),
            "note_path": str(vault / "lenzing_competitive.md"),
        },
    ]

    for note in notes:
        conn.execute(
            """INSERT INTO notes
               (project_id, client, title, type, source_type,
                topics, products, domains, note_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                note["project_id"], note["client"], note["title"],
                note["type"], note["source_type"],
                note["topics"], note["products"], note["domains"],
                note["note_path"],
            ),
        )

    conn.commit()
    conn.close()

    # Write note files with frontmatter + content
    (vault / "lenzing_discovery.md").write_text(
        "---\ntitle: Lenzing Discovery Workshop Notes\nclient: Lenzing\n---\n\n"
        "## Meeting Notes\n\nDiscussed demand planning requirements. "
        "Lenzing needs forecasting for fiber production. "
        "Current system is SAP APO, looking to modernize.",
        encoding="utf-8",
    )
    (vault / "lenzing_rfp.md").write_text(
        "---\ntitle: Lenzing RFP Response Architecture\n---\n\n"
        "## Architecture\n\nProposed solution uses Cognitive Demand Planning "
        "with Platform integration. Key requirement: SAP S/4HANA integration.",
        encoding="utf-8",
    )
    (vault / "sgdbf_wms.md").write_text(
        "---\ntitle: SGDBF WMS Implementation Requirements\n---\n\n"
        "## Requirements\n\nSGDBF needs WMS for 200+ retail stores. "
        "Focus on inventory accuracy and replenishment.",
        encoding="utf-8",
    )
    (vault / "platform_arch.md").write_text(
        "---\ntitle: Platform Architecture Overview\n---\n\n"
        "## Platform Overview\n\nBlue Yonder Platform is a cloud-native "
        "microservices architecture supporting all BY products.",
        encoding="utf-8",
    )
    (vault / "lenzing_competitive.md").write_text(
        "---\ntitle: Lenzing Competitive Analysis\n---\n\n"
        "## Competitive Landscape\n\nLenzing is also evaluating SAP IBP. "
        "Our advantage: ML-driven forecasting accuracy.",
        encoding="utf-8",
    )

    return db_path


@pytest.fixture()
def vault_root(tmp_path: Path) -> Path:
    return tmp_path / "vault"


class TestRetrieveBasic:
    def test_basic_fts_search(self, test_db: Path, vault_root: Path) -> None:
        """Basic FTS5 retrieval returns matching notes."""
        result = retrieve("Lenzing", test_db, vault_root)
        assert len(result.notes) >= 2
        assert all("Lenzing" in n.title for n in result.notes[:3])

    def test_client_filter(self, test_db: Path, vault_root: Path) -> None:
        """Client filter narrows results to matching client."""
        result = retrieve(
            "planning",
            test_db, vault_root,
            filters=RetrievalFilter(client="Lenzing"),
        )
        assert all(n.client == "Lenzing" for n in result.notes)

    def test_product_filter(self, test_db: Path, vault_root: Path) -> None:
        """Product filter narrows results."""
        result = retrieve(
            "architecture",
            test_db, vault_root,
            filters=RetrievalFilter(products=["WMS"]),
        )
        for note in result.notes:
            assert any("WMS" in p for p in note.products)

    def test_no_results(self, test_db: Path, vault_root: Path) -> None:
        """Query with no matches returns empty with gap."""
        result = retrieve("xyznonexistent", test_db, vault_root)
        assert len(result.notes) == 0
        assert not result.sufficient
        assert any("No results" in g for g in result.coverage_gaps)

    def test_loads_note_content(self, test_db: Path, vault_root: Path) -> None:
        """Retrieved notes include full markdown content."""
        result = retrieve("Lenzing Discovery", test_db, vault_root)
        assert len(result.notes) >= 1
        note = result.notes[0]
        assert "demand planning" in note.content.lower()

    def test_strips_frontmatter(self, test_db: Path, vault_root: Path) -> None:
        """Note content returned without YAML frontmatter."""
        result = retrieve("Lenzing Discovery", test_db, vault_root)
        note = result.notes[0]
        assert "---" not in note.content
        assert "title:" not in note.content

    def test_top_n_limit(self, test_db: Path, vault_root: Path) -> None:
        """Results capped at top_n."""
        result = retrieve("Lenzing", test_db, vault_root, top_n=2)
        assert len(result.notes) <= 2

    def test_sufficient_flag(self, test_db: Path, vault_root: Path) -> None:
        """sufficient=True when >= min_results."""
        result = retrieve(
            "Lenzing", test_db, vault_root,
            min_results_for_sufficient=2,
        )
        assert result.sufficient is True

        result2 = retrieve(
            "Lenzing", test_db, vault_root,
            min_results_for_sufficient=100,
        )
        assert result2.sufficient is False

    def test_coverage_gaps(self, test_db: Path, vault_root: Path) -> None:
        """Coverage gaps identify missing products."""
        result = retrieve(
            "Lenzing",
            test_db, vault_root,
            filters=RetrievalFilter(
                client="Lenzing",
                products=["TMS"],  # Lenzing has no TMS notes
            ),
        )
        assert any("TMS" in g for g in result.coverage_gaps)

    def test_citation_format(self, test_db: Path, vault_root: Path) -> None:
        """Citations follow [Title] (client: X, source: Y) format."""
        result = retrieve("Lenzing Discovery", test_db, vault_root)
        note = result.notes[0]
        assert note.citation.startswith("[Lenzing Discovery")
        assert "client: Lenzing" in note.citation

    def test_metadata_only_results(
        self, test_db: Path, vault_root: Path,
    ) -> None:
        """Client filter returns notes even without FTS match."""
        result = retrieve(
            "xyznonexistent",
            test_db, vault_root,
            filters=RetrievalFilter(client="SGDBF"),
        )
        # Should find SGDBF notes via metadata even though FTS found nothing
        assert len(result.notes) >= 1
        assert result.notes[0].client == "SGDBF"

    def test_result_has_parsed_json_fields(
        self, test_db: Path, vault_root: Path,
    ) -> None:
        """Topics, products, domains are parsed from JSON."""
        result = retrieve("Lenzing Discovery", test_db, vault_root)
        note = result.notes[0]
        assert isinstance(note.topics, list)
        assert isinstance(note.products, list)
        assert "Demand Planning" in note.topics or "Forecasting" in note.topics


class TestBuildFtsQuery:
    def test_removes_stopwords(self) -> None:
        """FTS query builder removes common stopwords."""
        q = _build_fts_query("what is the Platform Architecture")
        assert '"what"' not in q
        assert '"the"' not in q
        assert '"platform"' in q
        assert '"architecture"' in q

    def test_preserves_keywords(self) -> None:
        """Important keywords preserved after stopword removal."""
        q = _build_fts_query("WMS integration requirements")
        assert '"wms"' in q
        assert '"integration"' in q
        assert '"requirements"' in q

    def test_handles_all_stopwords(self) -> None:
        """Query of only stopwords keeps first few words."""
        q = _build_fts_query("the a an is")
        assert q  # Should not be empty

    def test_cleans_special_chars(self) -> None:
        """Special characters removed from query terms."""
        q = _build_fts_query("WMS-integration (v2)")
        assert "(" not in q
        assert ")" not in q
        assert "-" not in q

    def test_empty_query(self) -> None:
        """Empty query returns something usable."""
        q = _build_fts_query("")
        assert q


class TestHelpers:
    def test_load_note_content_strips_frontmatter(self, tmp_path: Path) -> None:
        """Frontmatter is stripped from note content."""
        note = tmp_path / "note.md"
        note.write_text(
            "---\ntitle: Test\ntopics: [A]\n---\n\n## Content\nBody text here.",
            encoding="utf-8",
        )
        content = _load_note_content(note)
        assert "title:" not in content
        assert "Body text here" in content

    def test_load_note_content_no_frontmatter(self, tmp_path: Path) -> None:
        """Notes without frontmatter return full content."""
        note = tmp_path / "note.md"
        note.write_text("# Title\n\nBody text.", encoding="utf-8")
        content = _load_note_content(note)
        assert "Title" in content

    def test_load_note_content_missing_file(self, tmp_path: Path) -> None:
        """Missing note file returns empty string."""
        content = _load_note_content(tmp_path / "nonexistent.md")
        assert content == ""

    def test_parse_json_field(self) -> None:
        """Parses JSON array string."""
        assert _parse_json_field('["WMS", "TMS"]') == ["WMS", "TMS"]

    def test_parse_json_field_empty(self) -> None:
        """None/empty returns empty list."""
        assert _parse_json_field(None) == []
        assert _parse_json_field("") == []

    def test_parse_json_field_comma_fallback(self) -> None:
        """Falls back to comma-separated parsing."""
        assert _parse_json_field("WMS, TMS, Planning") == ["WMS", "TMS", "Planning"]

    def test_find_coverage_gaps_products(self) -> None:
        """Identifies missing products."""
        filters = RetrievalFilter(products=["WMS", "TMS"])
        notes = [
            RetrievedNote(
                note_id=1, title="t", client="c", project_id="p",
                topics=[], products=["WMS"], domains=[],
                source_type="", note_type="", note_path="",
                content="", relevance_score=0, citation="",
            ),
        ]
        gaps = _find_coverage_gaps("q", filters, notes)
        assert any("TMS" in g for g in gaps)
        assert not any("WMS" in g for g in gaps)

    def test_find_coverage_gaps_empty(self) -> None:
        """Empty results produce 'No results' gap."""
        gaps = _find_coverage_gaps("q", RetrievalFilter(), [])
        assert any("No results" in g for g in gaps)


class TestFallbackSearch:
    def test_fallback_on_fts_error(self, test_db: Path, vault_root: Path) -> None:
        """Malformed FTS5 query falls back to LIKE search."""
        # This should not raise — fallback handles it
        result = retrieve(
            "NEAR(broken query",
            test_db, vault_root,
        )
        assert isinstance(result, RetrievalResult)
