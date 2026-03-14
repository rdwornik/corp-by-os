"""Tests for JSON output format of corp retrieve."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from corp_by_os.cli import cli


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
def json_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create test DB and patch config for CLI testing."""
    db_path = tmp_path / "index.db"
    vault = tmp_path / "vault"
    vault.mkdir()

    conn = sqlite3.connect(str(db_path))
    conn.executescript(_TEST_SCHEMA)

    note_file = vault / "platform_arch.md"
    note_file.write_text(
        "---\ntitle: Platform Architecture\ntrust_level: verified\n"
        "extracted_at: '2026-01-15'\n---\n\n"
        "Blue Yonder Platform is cloud-native microservices.",
        encoding="utf-8",
    )

    conn.execute(
        """INSERT INTO notes
           (project_id, client, title, type, source_type,
            topics, products, domains, confidence, note_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "general", "", "Platform Architecture",
            "training", "documentation",
            json.dumps(["Architecture", "Platform"]),
            json.dumps(["Platform"]),
            json.dumps(["Platform"]),
            "verified",
            str(note_file),
        ),
    )
    conn.commit()
    conn.close()

    # Patch get_index_path and get_config
    monkeypatch.setattr(
        "corp_by_os.cli.get_config",
        lambda: type("Cfg", (), {"vault_path": vault, "mywork_root": tmp_path})(),
    )

    import corp_by_os.index_builder
    monkeypatch.setattr(corp_by_os.index_builder, "get_index_path", lambda: db_path)

    return db_path


class TestJSONFormat:
    def test_outputs_valid_json(self, json_db: Path) -> None:
        """--format json outputs valid parseable JSON."""
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "Platform", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_has_required_fields(self, json_db: Path) -> None:
        """JSON includes all required fields."""
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "Platform", "--format", "json"])
        data = json.loads(result.output)

        assert "query" in data
        assert "total_found" in data
        assert "sufficient" in data
        assert "coverage_gaps" in data
        assert "notes" in data

        if data["notes"]:
            note = data["notes"][0]
            for field in [
                "note_id", "title", "content", "products", "topics",
                "relevance_score", "confidence", "overlay_data",
                "source_path", "extracted_at", "citation",
            ]:
                assert field in note, f"Missing field: {field}"

    def test_no_rich_formatting(self, json_db: Path) -> None:
        """JSON mode produces no Rich table markup."""
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "Platform", "--format", "json"])
        # Rich tables use box-drawing characters
        assert "┌" not in result.output
        assert "│" not in result.output
        assert "[bold]" not in result.output
        assert "[cyan]" not in result.output

    def test_empty_results(self, json_db: Path) -> None:
        """Empty results produce valid JSON with empty notes list."""
        runner = CliRunner()
        result = runner.invoke(
            cli, ["retrieve", "xyznonexistent", "--format", "json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["notes"] == []
        assert data["total_found"] == 0

    def test_confidence_in_json(self, json_db: Path) -> None:
        """JSON output includes confidence field."""
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "Platform", "--format", "json"])
        data = json.loads(result.output)
        if data["notes"]:
            assert data["notes"][0]["confidence"] == "verified"

    def test_overlay_data_in_json(self, json_db: Path) -> None:
        """JSON output includes overlay_data field (empty dict if none)."""
        runner = CliRunner()
        result = runner.invoke(cli, ["retrieve", "Platform", "--format", "json"])
        data = json.loads(result.output)
        if data["notes"]:
            assert isinstance(data["notes"][0]["overlay_data"], dict)
