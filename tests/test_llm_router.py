"""Tests for llm_router module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corp_by_os.llm_router import (
    _build_workflows_summary,
    _check_daily_cap,
    _extract_json,
    _increment_usage,
    _load_usage,
    _parse_llm_response,
    _save_usage,
    classify_intent,
)
from corp_by_os.models import Workflow, WorkflowParam


# --- Fixtures ---


@pytest.fixture()
def simple_workflows() -> dict[str, Workflow]:
    return {
        "attention_scan": Workflow(
            id="attention_scan",
            description="Scan all projects",
            parameters={},
        ),
        "new_opportunity": Workflow(
            id="new_opportunity",
            description="Create new opportunity",
            parameters={
                "client": WorkflowParam(type="string", required=True),
                "product": WorkflowParam(type="string", required=True),
            },
        ),
    }


# --- Test: JSON Parsing ---


class TestParseLLMResponse:
    def test_valid_json(self) -> None:
        response = json.dumps({
            "workflow_id": "attention_scan",
            "parameters": {},
            "confidence": 0.95,
            "response_text": None,
        })
        intent = _parse_llm_response(response)
        assert intent.workflow_id == "attention_scan"
        assert intent.confidence == 0.95
        assert intent.source == "llm"

    def test_json_with_params(self) -> None:
        response = json.dumps({
            "workflow_id": "new_opportunity",
            "parameters": {"client": "Bosch", "product": "WMS", "contact": None},
            "confidence": 0.9,
            "response_text": None,
        })
        intent = _parse_llm_response(response)
        assert intent.parameters == {"client": "Bosch", "product": "WMS"}
        assert "contact" not in intent.parameters  # null filtered out

    def test_json_in_code_fence(self) -> None:
        response = '```json\n{"workflow_id": "attention_scan", "parameters": {}, "confidence": 0.8, "response_text": null}\n```'
        intent = _parse_llm_response(response)
        assert intent.workflow_id == "attention_scan"

    def test_chitchat_response(self) -> None:
        response = json.dumps({
            "workflow_id": None,
            "parameters": {},
            "confidence": 0.1,
            "response_text": "Hej! Czym mogę pomóc?",
        })
        intent = _parse_llm_response(response)
        assert intent.workflow_id is None
        assert intent.response_text == "Hej! Czym mogę pomóc?"

    def test_invalid_json(self) -> None:
        intent = _parse_llm_response("This is not JSON at all")
        assert intent.confidence == 0.0

    def test_json_embedded_in_text(self) -> None:
        response = 'Here is the result: {"workflow_id": "attention_scan", "parameters": {}, "confidence": 0.7, "response_text": null} done.'
        intent = _parse_llm_response(response)
        assert intent.workflow_id == "attention_scan"


# --- Test: Extract JSON ---


class TestExtractJson:
    def test_extract_from_text(self) -> None:
        text = 'blah {"key": "value"} blah'
        assert _extract_json(text) == '{"key": "value"}'

    def test_no_json(self) -> None:
        assert _extract_json("no json here") is None

    def test_invalid_json_braces(self) -> None:
        assert _extract_json("{ broken json }") is None


# --- Test: Usage Tracking ---


class TestUsageTracking:
    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        _save_usage(path, {"date": "2026-03-09", "calls": 5})
        data = _load_usage(path)
        assert data["calls"] == 5

    def test_load_missing(self, tmp_path: Path) -> None:
        data = _load_usage(tmp_path / "nonexistent.json")
        assert data == {}

    @patch("corp_by_os.llm_router._get_usage_path")
    def test_daily_cap_under(self, mock_path, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        _save_usage(path, {"date": "2026-03-09", "calls": 5})
        mock_path.return_value = path
        assert _check_daily_cap() is True

    @patch("corp_by_os.llm_router._get_usage_path")
    def test_daily_cap_reached(self, mock_path, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("LLM_DAILY_CAP", "3")
        path = tmp_path / "usage.json"
        from datetime import date
        _save_usage(path, {"date": date.today().isoformat(), "calls": 3})
        mock_path.return_value = path
        assert _check_daily_cap() is False

    @patch("corp_by_os.llm_router._get_usage_path")
    def test_new_day_resets(self, mock_path, tmp_path: Path) -> None:
        path = tmp_path / "usage.json"
        _save_usage(path, {"date": "2026-03-01", "calls": 100})
        mock_path.return_value = path
        # New day — should be under cap
        assert _check_daily_cap() is True


# --- Test: Workflows Summary ---


class TestWorkflowsSummary:
    def test_builds_summary(self, simple_workflows) -> None:
        summary = _build_workflows_summary(simple_workflows)
        assert "attention_scan" in summary
        assert "new_opportunity" in summary
        assert "client (required)" in summary


# --- Test: Classify Intent (mocked) ---


class TestClassifyIntent:
    @patch("corp_by_os.llm_router._check_daily_cap", return_value=False)
    def test_cap_reached(self, mock_cap, simple_workflows) -> None:
        intent = classify_intent("hello", simple_workflows)
        assert intent.workflow_id is None
        assert "Limit" in intent.response_text or "limit" in intent.response_text.lower()

    @patch.dict("os.environ", {"GEMINI_API_KEY": ""})
    @patch("corp_by_os.llm_router._check_daily_cap", return_value=True)
    def test_missing_api_key(self, mock_cap, simple_workflows) -> None:
        intent = classify_intent("hello", simple_workflows)
        assert intent.workflow_id is None
        assert "GEMINI_API_KEY" in intent.response_text

    @patch("corp_by_os.llm_router._check_daily_cap", return_value=True)
    @patch("corp_by_os.llm_router._increment_usage")
    def test_successful_call(self, mock_incr, mock_cap, simple_workflows, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "workflow_id": "attention_scan",
            "parameters": {},
            "confidence": 0.9,
            "response_text": None,
        })

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            intent = classify_intent("co się dzieje z projektami", simple_workflows)

        assert intent.workflow_id == "attention_scan"
        assert intent.source == "llm"
        mock_incr.assert_called_once()

    @patch("corp_by_os.llm_router._check_daily_cap", return_value=True)
    def test_api_failure(self, mock_cap, simple_workflows, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")

        with patch("google.genai.Client", side_effect=Exception("API error")):
            intent = classify_intent("hello", simple_workflows)

        assert intent.workflow_id is None
        assert "Błąd" in intent.response_text or "error" in intent.response_text.lower()
