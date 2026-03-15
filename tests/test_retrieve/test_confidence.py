"""Tests for confidence-aware ranking."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from corp_by_os.retrieve.engine import (
    CONFIDENCE_BOOST,
    RetrievalFilter,
    RetrievedNote,
    _apply_confidence_ranking,
    retrieve,
)


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


def _make_note(
    note_id: int = 1,
    title: str = "Test",
    confidence: str = "extracted",
    relevance_score: float = 0.0,
) -> RetrievedNote:
    return RetrievedNote(
        note_id=note_id,
        title=title,
        client="",
        project_id="test",
        topics=[],
        products=[],
        domains=[],
        source_type="",
        note_type="",
        note_path="/fake.md",
        content="test",
        relevance_score=relevance_score,
        citation=f"[{title}]",
        confidence=confidence,
    )


class TestConfidenceBoostValues:
    def test_boost_values_correct(self) -> None:
        """Boost values: verified=0, extracted=10, generated=50, draft=100."""
        assert CONFIDENCE_BOOST["verified"] == 0
        assert CONFIDENCE_BOOST["extracted"] == 10
        assert CONFIDENCE_BOOST["generated"] == 50
        assert CONFIDENCE_BOOST["draft"] == 100

    def test_verified_has_lowest_boost(self) -> None:
        """Verified is the most trusted (lowest penalty)."""
        assert CONFIDENCE_BOOST["verified"] < CONFIDENCE_BOOST["extracted"]
        assert CONFIDENCE_BOOST["extracted"] < CONFIDENCE_BOOST["generated"]
        assert CONFIDENCE_BOOST["generated"] < CONFIDENCE_BOOST["draft"]


class TestApplyConfidenceRanking:
    def test_verified_beats_draft(self) -> None:
        """Verified note ranks higher than draft at similar BM25."""
        notes = [
            _make_note(note_id=1, title="Draft", confidence="draft", relevance_score=-5.0),
            _make_note(note_id=2, title="Verified", confidence="verified", relevance_score=-5.0),
        ]
        ranked = _apply_confidence_ranking(notes)
        assert ranked[0].title == "Verified"
        assert ranked[1].title == "Draft"

    def test_highly_relevant_draft_beats_irrelevant_verified(self) -> None:
        """A very relevant draft can still outrank an irrelevant verified note."""
        notes = [
            _make_note(
                note_id=1, title="Irrelevant Verified", confidence="verified", relevance_score=500.0
            ),
            _make_note(
                note_id=2, title="Relevant Draft", confidence="draft", relevance_score=-200.0
            ),
        ]
        ranked = _apply_confidence_ranking(notes)
        # Draft(-200 + 100 = -100) beats Verified(500 + 0 = 500)
        assert ranked[0].title == "Relevant Draft"

    def test_same_confidence_preserves_relevance_order(self) -> None:
        """Notes with same confidence are sorted by BM25."""
        notes = [
            _make_note(
                note_id=1, title="Less Relevant", confidence="extracted", relevance_score=10.0
            ),
            _make_note(
                note_id=2, title="More Relevant", confidence="extracted", relevance_score=-5.0
            ),
        ]
        ranked = _apply_confidence_ranking(notes)
        assert ranked[0].title == "More Relevant"


class TestConfidenceInRetrieve:
    def test_default_confidence_extracted(self, tmp_path: Path) -> None:
        """Notes without confidence column default to 'extracted'."""
        db_path = tmp_path / "index.db"
        vault = tmp_path / "vault"
        vault.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.executescript(_TEST_SCHEMA)

        note_file = vault / "test.md"
        note_file.write_text(
            "---\ntitle: Test Note\n---\n\nSome content about platform.",
            encoding="utf-8",
        )

        conn.execute(
            """INSERT INTO notes
               (project_id, client, title, type, source_type,
                topics, products, domains, note_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "test",
                "",
                "Test Note",
                "notes",
                "doc",
                json.dumps(["Platform"]),
                json.dumps(["Platform"]),
                json.dumps(["General"]),
                str(note_file),
            ),
        )
        conn.commit()
        conn.close()

        result = retrieve("platform", db_path, vault)
        assert len(result.notes) >= 1
        assert result.notes[0].confidence == "extracted"

    def test_confidence_from_db(self, tmp_path: Path) -> None:
        """Notes with confidence column use it."""
        db_path = tmp_path / "index.db"
        vault = tmp_path / "vault"
        vault.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.executescript(_TEST_SCHEMA)

        note_file = vault / "verified.md"
        note_file.write_text(
            "---\ntitle: Verified Note\ntrust_level: verified\n---\n\nVerified content.",
            encoding="utf-8",
        )

        conn.execute(
            """INSERT INTO notes
               (project_id, client, title, type, source_type,
                topics, products, domains, confidence, note_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "test",
                "",
                "Verified Note",
                "notes",
                "doc",
                json.dumps(["Security"]),
                json.dumps([]),
                json.dumps(["General"]),
                "verified",
                str(note_file),
            ),
        )
        conn.commit()
        conn.close()

        result = retrieve("verified security", db_path, vault)
        assert len(result.notes) >= 1
        assert result.notes[0].confidence == "verified"
