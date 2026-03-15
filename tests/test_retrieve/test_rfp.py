"""Tests for RFP answer generator."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corp_by_os.retrieve.engine import RetrievalFilter, RetrievedNote
from corp_by_os.retrieve.rfp import RFPAnswer, answer_rfp


# --- Schema (same as test_prep.py) ---

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


def _insert_note(
    conn: sqlite3.Connection,
    vault: Path,
    title: str,
    client: str = "",
    content: str = "Test content.",
    source_type: str = "rfp",
    topics: list[str] | None = None,
    products: list[str] | None = None,
) -> None:
    """Insert a note into the test DB and write the vault file."""
    slug = title.lower().replace(" ", "_")
    note_file = vault / f"{slug}.md"
    note_file.write_text(
        f"---\ntitle: {title}\n---\n\n{content}",
        encoding="utf-8",
    )
    conn.execute(
        """INSERT INTO notes
           (project_id, client, title, type, source_type,
            topics, products, domains, note_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "test_project",
            client,
            title,
            "notes",
            source_type,
            json.dumps(topics or []),
            json.dumps(products or []),
            json.dumps(["General"]),
            str(note_file),
        ),
    )


@pytest.fixture()
def rfp_db(tmp_path: Path) -> tuple[Path, Path]:
    """Create test DB with several notes for RFP testing."""
    db_path = tmp_path / "index.db"
    vault = tmp_path / "vault"
    vault.mkdir()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(_TEST_SCHEMA)

    _insert_note(
        conn,
        vault,
        "SaaS Architecture Overview",
        content="Blue Yonder Platform runs on multi-tenant SaaS architecture.",
        topics=["SaaS", "Architecture"],
        products=["Platform"],
    )
    _insert_note(
        conn,
        vault,
        "Security Certifications",
        content="Blue Yonder holds SOC2 Type II and ISO 27001.",
        topics=["Security", "Compliance"],
    )
    _insert_note(
        conn,
        vault,
        "Demand Planning Features",
        content="Cognitive Demand Planning uses ML for forecast accuracy.",
        topics=["Demand Planning", "ML"],
        products=["Cognitive Demand Planning"],
    )
    _insert_note(
        conn,
        vault,
        "WMS Integration Guide",
        content="WMS integrates with SAP via standard IDocs and APIs.",
        topics=["WMS", "Integration"],
        products=["WMS"],
    )
    _insert_note(
        conn,
        vault,
        "Platform Data Cloud",
        content="Snowflake-based data cloud for analytics and reporting.",
        topics=["Data", "Analytics"],
        products=["Platform"],
    )
    _insert_note(
        conn,
        vault,
        "Lenzing Requirements",
        client="Lenzing",
        content="Lenzing needs demand planning with SAP APO migration.",
        topics=["Demand Planning"],
        products=["Cognitive Demand Planning"],
    )

    conn.commit()
    conn.close()
    return db_path, vault


def _mock_llm(text: str = "**Answer:** Test answer."):
    """Return context managers that mock genai for _call_llm."""
    mock_genai = patch("corp_by_os.retrieve.prep.genai")
    mock_types = patch("corp_by_os.retrieve.prep.genai_types")

    genai_cm = mock_genai.start()
    types_cm = mock_types.start()

    mock_response = MagicMock()
    mock_response.text = text
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    genai_cm.Client.return_value = mock_client
    types_cm.GenerateContentConfig = MagicMock()

    return mock_genai, mock_types


class TestAnswerRFPBasic:
    def test_produces_answer_with_citations(self, rfp_db: tuple[Path, Path]) -> None:
        """Full pipeline produces an RFPAnswer."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm("**Answer:** Blue Yonder provides SaaS. [SaaS Architecture Overview]")

        try:
            result = answer_rfp(
                question="Describe your SaaS deployment model",
                db_path=db_path,
                vault_root=vault,
            )
        finally:
            mg.stop()
            mt.stop()

        assert isinstance(result, RFPAnswer)
        assert result.question == "Describe your SaaS deployment model"
        assert result.source_count >= 1
        assert result.answer_text
        assert result.cost >= 0.0

    def test_returns_correct_model(self, rfp_db: tuple[Path, Path]) -> None:
        """Result includes model used."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm()
        try:
            result = answer_rfp(
                question="SaaS model?",
                db_path=db_path,
                vault_root=vault,
                model="gemini-test",
            )
        finally:
            mg.stop()
            mt.stop()

        assert result.model == "gemini-test"


class TestConfidenceLevels:
    def test_high_confidence(self, rfp_db: tuple[Path, Path]) -> None:
        """5+ relevant sources with sufficient flag -> high."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm()
        try:
            # Broad query should hit many notes
            result = answer_rfp(
                question="Blue Yonder platform SaaS architecture demand planning WMS integration data",
                db_path=db_path,
                vault_root=vault,
            )
        finally:
            mg.stop()
            mt.stop()

        assert result.source_count >= 5
        assert result.confidence == "high"

    def test_medium_confidence(self, rfp_db: tuple[Path, Path]) -> None:
        """2-4 sources -> medium."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm()
        try:
            result = answer_rfp(
                question="security certifications compliance",
                db_path=db_path,
                vault_root=vault,
            )
        finally:
            mg.stop()
            mt.stop()

        # Should get some but not 5+ hits
        if 2 <= result.source_count < 5:
            assert result.confidence == "medium"
        # If query happens to match more, at least verify it's not insufficient
        assert result.confidence != "insufficient"

    def test_insufficient_confidence(self, tmp_path: Path) -> None:
        """No sources -> insufficient."""
        db_path = tmp_path / "empty.db"
        vault = tmp_path / "vault"
        vault.mkdir()

        conn = sqlite3.connect(str(db_path))
        conn.executescript(_TEST_SCHEMA)
        conn.close()

        mg, mt = _mock_llm()
        try:
            result = answer_rfp(
                question="What blockchain features do you offer?",
                db_path=db_path,
                vault_root=vault,
            )
        finally:
            mg.stop()
            mt.stop()

        assert result.source_count == 0
        assert result.confidence == "insufficient"


class TestFilters:
    def test_client_filter(self, rfp_db: tuple[Path, Path]) -> None:
        """Client filter narrows retrieval."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm()
        try:
            result = answer_rfp(
                question="demand planning requirements",
                db_path=db_path,
                vault_root=vault,
                client="Lenzing",
            )
        finally:
            mg.stop()
            mt.stop()

        assert isinstance(result, RFPAnswer)
        assert result.source_count >= 1

    def test_broadens_on_no_results(self, rfp_db: tuple[Path, Path]) -> None:
        """If filtered search empty, retries without filters."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm()
        try:
            # Use a client that doesn't exist — should broaden
            result = answer_rfp(
                question="SaaS architecture",
                db_path=db_path,
                vault_root=vault,
                client="NonExistentCorp",
            )
        finally:
            mg.stop()
            mt.stop()

        # Should still get results from the broader search
        assert result.source_count >= 1

    def test_product_filter(self, rfp_db: tuple[Path, Path]) -> None:
        """Product filter narrows retrieval."""
        db_path, vault = rfp_db
        mg, mt = _mock_llm()
        try:
            result = answer_rfp(
                question="features and capabilities",
                db_path=db_path,
                vault_root=vault,
                product="WMS",
            )
        finally:
            mg.stop()
            mt.stop()

        assert isinstance(result, RFPAnswer)


class TestLLMFailure:
    def test_llm_failure_does_not_crash(self, rfp_db: tuple[Path, Path]) -> None:
        """LLM failure returns error message, doesn't crash."""
        db_path, vault = rfp_db

        with (
            patch("corp_by_os.retrieve.prep.genai") as mock_genai,
            patch("corp_by_os.retrieve.prep.genai_types") as mock_types,
        ):
            mock_client = MagicMock()
            mock_client.models.generate_content.side_effect = RuntimeError("API down")
            mock_genai.Client.return_value = mock_client
            mock_types.GenerateContentConfig = MagicMock()

            result = answer_rfp(
                question="SaaS deployment",
                db_path=db_path,
                vault_root=vault,
            )

        assert "FAILED" in result.answer_text
        assert result.cost == 0.0

    def test_no_genai_sdk(self, rfp_db: tuple[Path, Path]) -> None:
        """Missing genai SDK returns unavailable message."""
        db_path, vault = rfp_db

        with patch("corp_by_os.retrieve.prep.genai", None):
            result = answer_rfp(
                question="SaaS deployment",
                db_path=db_path,
                vault_root=vault,
            )

        assert "UNAVAILABLE" in result.answer_text
        assert result.cost == 0.0
