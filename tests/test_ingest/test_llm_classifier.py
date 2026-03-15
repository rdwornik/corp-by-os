"""Tests for LLM fallback classifier."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from corp_by_os.ingest.llm_classifier import (
    LLMClassification,
    _get_all_destinations,
    _parse_llm_json,
    classify_file_llm,
    classify_quarantined_batch,
)
from corp_by_os.ops.database import OpsDB
from corp_by_os.ops.registry import ContentRegistry


@pytest.fixture()
def registry(tmp_path: Path) -> ContentRegistry:
    data = {
        "version": "1.0",
        "series": {
            "cognitive_friday": {
                "display_name": "Cognitive Friday",
                "destination": "60_Source_Library/02_Training_Enablement/Cognitive_Friday",
                "naming_patterns": ["Cognitive_Friday*"],
            },
        },
        "destination_rules": [
            {
                "name": "RFP databases",
                "match": {"filename_contains": ["RFP_Database"]},
                "destination": "50_RFP/_databases",
            },
        ],
        "client_patterns": [],
        "fallback": {
            "unknown_destination": "00_Inbox/_Unmatched",
            "confidence_threshold": 0.75,
        },
    }
    path = tmp_path / "content_registry.yaml"
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return ContentRegistry(path)


@pytest.fixture()
def ops(tmp_path: Path) -> OpsDB:
    db = OpsDB(db_path=tmp_path / "test_ops.db")
    _ = db.conn
    yield db
    db.close()


class TestParseLlmJson:
    def test_direct_json(self) -> None:
        """Parses plain JSON string."""
        result = _parse_llm_json('{"destination": "10_Projects", "confidence": 0.8}')
        assert result is not None
        assert result["destination"] == "10_Projects"

    def test_code_fence_json(self) -> None:
        """Parses JSON inside markdown code fence."""
        raw = '```json\n{"destination": "50_RFP", "confidence": 0.7}\n```'
        result = _parse_llm_json(raw)
        assert result is not None
        assert result["destination"] == "50_RFP"

    def test_json_with_prose(self) -> None:
        """Extracts JSON from surrounding prose."""
        raw = 'Here is my analysis:\n{"destination": "10_Projects", "confidence": 0.6}\nDone.'
        result = _parse_llm_json(raw)
        assert result is not None
        assert result["destination"] == "10_Projects"

    def test_unparseable(self) -> None:
        """Returns None for garbage input."""
        assert _parse_llm_json("this is not json at all") is None

    def test_empty_string(self) -> None:
        """Returns None for empty string."""
        assert _parse_llm_json("") is None


class TestClassifyFileLlm:
    def test_parses_valid_response(self) -> None:
        """classify_file_llm parses valid JSON from mocked Gemini."""
        mock_response = MagicMock()
        mock_response.text = '{"destination": "60_Source_Library/01_Product_Docs", "series_id": null, "topics": ["WMS"], "source_category": "product_doc", "confidence": 0.75, "reasoning": "Architecture doc"}'

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("corp_by_os.ingest.llm_classifier.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            result = classify_file_llm(
                "Architecture_Overview.pdf",
                ".pdf",
                2.5,
                "00_Inbox",
                None,
                ["60_Source_Library/01_Product_Docs"],
            )

        assert result.destination == "60_Source_Library/01_Product_Docs"
        assert result.source_category == "product_doc"
        assert result.confidence == 0.75

    def test_caps_confidence_at_085(self) -> None:
        """LLM confidence is capped at 0.85."""
        mock_response = MagicMock()
        mock_response.text = (
            '{"destination": "10_Projects", "confidence": 0.99, "reasoning": "Very sure"}'
        )

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("corp_by_os.ingest.llm_classifier.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            result = classify_file_llm(
                "test.pdf",
                ".pdf",
                1.0,
                "00_Inbox",
                None,
                [],
            )

        assert result.confidence == 0.85

    def test_handles_parse_failure(self) -> None:
        """Unparseable LLM response → quarantine with 0.0 confidence."""
        mock_response = MagicMock()
        mock_response.text = "I cannot determine the file type."

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("corp_by_os.ingest.llm_classifier.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            result = classify_file_llm(
                "mystery.bin",
                ".bin",
                0.5,
                "00_Inbox",
                None,
                [],
            )

        assert result.destination == "00_Inbox/_Unmatched"
        assert result.confidence == 0.0

    def test_handles_api_error(self) -> None:
        """API exception → quarantine with 0.0 confidence."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API down")

        with patch("corp_by_os.ingest.llm_classifier.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            result = classify_file_llm(
                "test.pdf",
                ".pdf",
                1.0,
                "00_Inbox",
                None,
                [],
            )

        assert result.destination == "00_Inbox/_Unmatched"
        assert result.confidence == 0.0
        assert "API error" in result.reasoning

    def test_handles_missing_sdk(self) -> None:
        """Missing google-genai SDK returns graceful fallback."""
        with patch("corp_by_os.ingest.llm_classifier.genai", None):
            result = classify_file_llm(
                "test.pdf",
                ".pdf",
                1.0,
                "00_Inbox",
                None,
                [],
            )
            assert result.destination == "00_Inbox/_Unmatched"
            assert result.confidence == 0.0


class TestClassifyQuarantinedBatch:
    def _add_quarantined(self, ops: OpsDB, filename: str) -> None:
        """Helper to add a quarantined asset to ops.db."""
        ops.upsert_asset(
            path=f"00_Inbox/_Unmatched/{filename}",
            filename=filename,
            extension=Path(filename).suffix,
            size_bytes=1024,
            mtime="2026-03-14T10:00:00",
            folder_l1="00_Inbox",
            folder_l2="_Unmatched",
        )
        ops.update_asset_status(
            f"00_Inbox/_Unmatched/{filename}",
            "quarantined",
        )

    def test_respects_budget(
        self,
        ops: OpsDB,
        registry: ContentRegistry,
        tmp_path: Path,
    ) -> None:
        """Batch classification stops when budget is exhausted."""
        # Add 5 quarantined files
        for i in range(5):
            self._add_quarantined(ops, f"file_{i}.pdf")

        mock_response = MagicMock()
        mock_response.text = (
            '{"destination": "00_Inbox/_Unmatched", "confidence": 0.3, "reasoning": "unsure"}'
        )
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("corp_by_os.ingest.llm_classifier.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            # Budget of $0.002 → should process only 2 files (at $0.001 each)
            results = classify_quarantined_batch(
                ops,
                registry,
                tmp_path,
                budget=0.002,
                dry_run=True,
            )

        assert len(results) == 2

    def test_dry_run_no_move(
        self,
        ops: OpsDB,
        registry: ContentRegistry,
        tmp_path: Path,
    ) -> None:
        """Dry run classifies but doesn't move files."""
        self._add_quarantined(ops, "test.pdf")

        # Create the actual file
        unmatched = tmp_path / "00_Inbox" / "_Unmatched"
        unmatched.mkdir(parents=True)
        (unmatched / "test.pdf").write_bytes(b"content")

        mock_response = MagicMock()
        mock_response.text = '{"destination": "60_Source_Library/01_Product_Docs", "confidence": 0.7, "reasoning": "looks like product doc"}'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("corp_by_os.ingest.llm_classifier.genai") as mock_genai:
            mock_genai.Client.return_value = mock_client
            results = classify_quarantined_batch(
                ops,
                registry,
                tmp_path,
                dry_run=True,
            )

        assert len(results) == 1
        # File should still be in _Unmatched
        assert (unmatched / "test.pdf").exists()
        # Status should still be quarantined
        asset = ops.get_asset("00_Inbox/_Unmatched/test.pdf")
        assert asset["status"] == "quarantined"

    def test_no_quarantined(
        self,
        ops: OpsDB,
        registry: ContentRegistry,
        tmp_path: Path,
    ) -> None:
        """Empty quarantine returns empty list."""
        results = classify_quarantined_batch(
            ops,
            registry,
            tmp_path,
            dry_run=True,
        )
        assert results == []


class TestGetAllDestinations:
    def test_includes_registry_and_standard(
        self,
        registry: ContentRegistry,
    ) -> None:
        """All valid destinations extracted from registry + standard folders."""
        dests = _get_all_destinations(registry)
        # From series
        assert "60_Source_Library/02_Training_Enablement/Cognitive_Friday" in dests
        # From rules
        assert "50_RFP/_databases" in dests
        # Standard
        assert "10_Projects" in dests
        assert "70_Admin" in dests

    def test_returns_sorted(self, registry: ContentRegistry) -> None:
        """Destinations are returned sorted."""
        dests = _get_all_destinations(registry)
        assert dests == sorted(dests)
