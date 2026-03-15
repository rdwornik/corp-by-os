"""Tests for client prep briefing generator."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corp_by_os.retrieve.engine import RetrievalFilter, RetrievedNote
from corp_by_os.retrieve.prep import (
    PrepBriefing,
    build_notes_context,
    generate_prep,
)


def _make_note(
    title: str = "Test Note",
    client: str = "TestClient",
    content: str = "Test content here.",
    source_type: str = "meeting",
    topics: list[str] | None = None,
    products: list[str] | None = None,
) -> RetrievedNote:
    """Helper to create a RetrievedNote for testing."""
    return RetrievedNote(
        note_id=1,
        title=title,
        client=client,
        project_id="test_project",
        topics=topics or ["Topic1"],
        products=products or ["Product1"],
        domains=["Domain1"],
        source_type=source_type,
        note_type="notes",
        note_path="/fake/path.md",
        content=content,
        relevance_score=0.5,
        citation=f"[{title}] (client: {client}, source: {source_type})",
    )


class TestBuildNotesContext:
    def test_formats_notes_with_headers(self) -> None:
        """Context builder formats notes with metadata headers."""
        notes = [_make_note(title="Discovery Notes", client="Lenzing")]
        ctx = build_notes_context(notes)
        assert "### [Discovery Notes]" in ctx
        assert "Client: Lenzing" in ctx
        assert "Test content here." in ctx

    def test_truncates_long_notes(self) -> None:
        """Long notes are truncated."""
        long_content = "x" * 5000
        notes = [_make_note(content=long_content)]
        ctx = build_notes_context(notes)
        assert "[... truncated ...]" in ctx
        assert len(ctx) < 5000

    def test_caps_total_context(self) -> None:
        """Total context capped at max limit."""
        # Create 50 notes with 2000 chars each — should be capped
        notes = [_make_note(title=f"Note {i}", content="y" * 1500) for i in range(50)]
        ctx = build_notes_context(notes)
        assert len(ctx) < 55000
        assert "more notes omitted" in ctx

    def test_empty_notes(self) -> None:
        """Empty notes list returns 'NO KNOWLEDGE' message."""
        ctx = build_notes_context([])
        assert "NO KNOWLEDGE" in ctx

    def test_includes_topics_and_products(self) -> None:
        """Context includes topics and products in headers."""
        notes = [
            _make_note(
                topics=["WMS", "Integration"],
                products=["Platform"],
            )
        ]
        ctx = build_notes_context(notes)
        assert "WMS" in ctx
        assert "Platform" in ctx


# --- Schema for test DB ---

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
    confidence TEXT,
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
"""


@pytest.fixture()
def prep_db(tmp_path: Path) -> tuple[Path, Path]:
    """Create test DB and vault for prep tests. Returns (db_path, vault_root)."""
    db_path = tmp_path / "index.db"
    vault = tmp_path / "vault"
    vault.mkdir()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(_TEST_SCHEMA)

    note_file = vault / "lenzing_notes.md"
    note_file.write_text(
        "---\ntitle: Lenzing Notes\n---\n\nLenzing needs demand planning. Current system: SAP APO.",
        encoding="utf-8",
    )

    conn.execute(
        """INSERT INTO notes
           (project_id, client, title, type, source_type,
            topics, products, domains, note_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "lenzing_planning",
            "Lenzing",
            "Lenzing Notes",
            "notes",
            "meeting",
            json.dumps(["Demand Planning"]),
            json.dumps(["Cognitive Demand Planning"]),
            json.dumps(["Planning"]),
            str(note_file),
        ),
    )
    conn.commit()
    conn.close()

    return db_path, vault


class TestGeneratePrep:
    def test_generates_briefing(self, prep_db: tuple[Path, Path]) -> None:
        """Full prep pipeline produces briefing with content."""
        db_path, vault = prep_db
        output_dir = vault.parent / "output"

        with (
            patch("corp_by_os.retrieve.prep.genai") as mock_genai,
            patch("corp_by_os.retrieve.prep.genai_types") as mock_types,
        ):
            mock_response = MagicMock()
            mock_response.text = "## Client Overview\n\nLenzing is a fiber company."
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client
            mock_types.GenerateContentConfig = MagicMock()

            briefing = generate_prep(
                "Lenzing",
                db_path,
                vault,
                output_dir=output_dir,
            )

        assert isinstance(briefing, PrepBriefing)
        assert briefing.source_count >= 1
        assert briefing.client == "Lenzing"
        assert "Client Overview" in briefing.briefing_text
        # Check file was saved
        saved = list(output_dir.glob("prep_Lenzing_*.md"))
        assert len(saved) == 1

    def test_saves_to_output_dir(self, prep_db: tuple[Path, Path]) -> None:
        """Briefing saved to specified output directory."""
        db_path, vault = prep_db
        output_dir = vault.parent / "custom_output"

        with (
            patch("corp_by_os.retrieve.prep.genai") as mock_genai,
            patch("corp_by_os.retrieve.prep.genai_types") as mock_types,
        ):
            mock_response = MagicMock()
            mock_response.text = "Briefing content"
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client
            mock_types.GenerateContentConfig = MagicMock()

            generate_prep(
                "Lenzing",
                db_path,
                vault,
                output_dir=output_dir,
            )

        assert output_dir.exists()
        assert len(list(output_dir.glob("*.md"))) == 1

    def test_handles_no_results(self, tmp_path: Path) -> None:
        """Prep with unknown client still produces output."""
        db_path = tmp_path / "empty.db"
        vault = tmp_path / "vault"
        vault.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.executescript(_TEST_SCHEMA)
        conn.close()

        with (
            patch("corp_by_os.retrieve.prep.genai") as mock_genai,
            patch("corp_by_os.retrieve.prep.genai_types") as mock_types,
        ):
            mock_response = MagicMock()
            mock_response.text = "No information available."
            mock_client = MagicMock()
            mock_client.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client
            mock_types.GenerateContentConfig = MagicMock()

            briefing = generate_prep(
                "UnknownCorp",
                db_path,
                vault,
            )

        assert briefing.source_count == 0
        assert any("No results" in g for g in briefing.coverage_gaps)

    def test_graceful_llm_failure(self, prep_db: tuple[Path, Path]) -> None:
        """LLM failure returns message asking for manual review."""
        db_path, vault = prep_db

        with (
            patch("corp_by_os.retrieve.prep.genai") as mock_genai,
            patch("corp_by_os.retrieve.prep.genai_types") as mock_types,
        ):
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = RuntimeError("API down")
            mock_genai.Client.return_value = mock_client
            mock_types.GenerateContentConfig = MagicMock()

            briefing = generate_prep(
                "Lenzing",
                db_path,
                vault,
            )

        assert "FAILED" in briefing.briefing_text
        assert "manually" in briefing.briefing_text.lower()
        assert briefing.cost == 0.0

    def test_no_genai_sdk(self, prep_db: tuple[Path, Path]) -> None:
        """Missing genai SDK returns unavailable message."""
        db_path, vault = prep_db

        with patch("corp_by_os.retrieve.prep.genai", None):
            briefing = generate_prep(
                "Lenzing",
                db_path,
                vault,
            )

        assert "UNAVAILABLE" in briefing.briefing_text
        assert briefing.cost == 0.0
