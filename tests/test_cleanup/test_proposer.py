"""Tests for cleanup proposer."""

from __future__ import annotations

from pathlib import Path

import yaml

from corp_by_os.cleanup.classifier import Classification
from corp_by_os.cleanup.scanner import FileInfo
from corp_by_os.cleanup.proposer import generate_proposals


def _make_classification(name: str, action: str, dest: str, confidence: float) -> Classification:
    """Helper to create a Classification for testing."""
    return Classification(
        file_info=FileInfo(
            path=Path(f"/fake/{name}"),
            name=name,
            extension=Path(name).suffix,
            size_bytes=1024,
            current_folder="00_Inbox",
            relative_path=f"00_Inbox/{name}",
        ),
        action=action,
        destination_folder=dest,
        proposed_name=name,
        reason="Test reason",
        confidence=confidence,
    )


def test_generate_proposals_yaml(tmp_path):
    """Proposals written as valid YAML with required fields."""
    classifications = [
        _make_classification("a.pptx", "move", "30_Templates/01_Presentation_Decks", 0.9),
        _make_classification("b.log", "delete", "DELETE", 0.8),
        _make_classification("c.txt", "keep", "00_Inbox", 0.5),
    ]

    out = tmp_path / "moves.yaml"
    generate_proposals(classifications, out)

    assert out.exists()
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert "moves" in data
    assert len(data["moves"]) == 3

    # Check required fields on each entry
    for entry in data["moves"]:
        assert "source" in entry
        assert "action" in entry
        assert "destination" in entry
        assert "confidence" in entry
        assert "approved" in entry
        assert entry["approved"] is None  # human fills this in


def test_proposals_sorted_by_confidence(tmp_path):
    """High confidence proposals appear first."""
    classifications = [
        _make_classification("low.txt", "move", "somewhere", 0.3),
        _make_classification("high.pptx", "move", "somewhere", 0.95),
        _make_classification("mid.pdf", "move", "somewhere", 0.7),
    ]

    out = tmp_path / "moves.yaml"
    generate_proposals(classifications, out)

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    confidences = [e["confidence"] for e in data["moves"]]
    assert confidences == sorted(confidences, reverse=True)


def test_proposals_summary(tmp_path):
    """Summary section has correct counts."""
    classifications = [
        _make_classification("a.pptx", "move", "dest", 0.9),
        _make_classification("b.log", "delete", "DELETE", 0.8),
        _make_classification("c.txt", "keep", "00_Inbox", 0.5),
        _make_classification("d.pptx", "move", "dest", 0.7),
    ]

    out = tmp_path / "moves.yaml"
    generate_proposals(classifications, out)

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 4
    assert data["summary"]["moves"] == 2
    assert data["summary"]["deletes"] == 1
    assert data["summary"]["keeps"] == 1
