"""Tests for intent_router module."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from corp_by_os.intent_router import (
    Intent,
    _extract_date,
    _extract_priority,
    _extract_product,
    _extract_reason,
    _next_weekday,
    _normalize,
    _strip_diacritics,
    _keyword_match,
    route,
)
from corp_by_os.models import Workflow, WorkflowParam, WorkflowStep


# --- Fixtures ---


@pytest.fixture()
def sample_workflows() -> dict[str, Workflow]:
    """Build a minimal set of workflows for testing."""
    return {
        "new_opportunity": Workflow(
            id="new_opportunity",
            description="Create new opportunity",
            trigger_phrases=["nowe opportunity", "new opportunity", "nowy klient", "new client"],
            parameters={
                "client": WorkflowParam(type="string", required=True),
                "product": WorkflowParam(type="string", required=True),
                "contact": WorkflowParam(type="string", required=False),
            },
            confirmation=True,
        ),
        "attention_scan": Workflow(
            id="attention_scan",
            description="Scan all projects for issues",
            trigger_phrases=["co wymaga uwagi", "what needs attention", "status", "przeglad"],
            parameters={},
        ),
        "prep_deck": Workflow(
            id="prep_deck",
            description="Prepare presentation",
            trigger_phrases=["przygotuj prezentacje", "prep deck", "prepare presentation"],
            parameters={
                "project": WorkflowParam(type="string", required=True),
                "topic": WorkflowParam(type="string", required=True),
                "date": WorkflowParam(type="string", required=False, default="today"),
            },
        ),
        "my_tasks": Workflow(
            id="my_tasks",
            description="Show current tasks",
            trigger_phrases=[
                "moje taski",
                "my tasks",
                "co mam zrobic",
                "what to do",
                "lista zadan",
            ],
            parameters={
                "status": WorkflowParam(type="string", required=False, default="todo"),
            },
        ),
        "add_task": Workflow(
            id="add_task",
            description="Create a task",
            trigger_phrases=["dodaj task", "add task"],
            parameters={
                "title": WorkflowParam(type="string", required=True),
                "project": WorkflowParam(type="string", required=False),
                "deadline": WorkflowParam(type="string", required=False),
                "priority": WorkflowParam(type="string", required=False, default="medium"),
            },
        ),
        "archive_project": Workflow(
            id="archive_project",
            description="Archive project",
            trigger_phrases=["archiwizuj", "archive", "zamknij projekt"],
            parameters={
                "project": WorkflowParam(type="string", required=True),
                "reason": WorkflowParam(type="string", required=True),
            },
            confirmation=True,
        ),
        "project_brief": Workflow(
            id="project_brief",
            description="Generate project brief",
            trigger_phrases=[
                "brief",
                "brief na",
                "prepare brief",
                "podsumowanie projektu",
                "co wiemy o",
            ],
            parameters={
                "project": WorkflowParam(type="string", required=True),
            },
        ),
        "extract_project": Workflow(
            id="extract_project",
            description="Full extraction pipeline",
            trigger_phrases=["przetworz projekt", "extract project", "wyciagnij wiedze"],
            parameters={
                "project": WorkflowParam(type="string", required=True),
            },
            confirmation=True,
        ),
    }


# --- Test: Normalization ---


class TestNormalize:
    def test_lowercase(self) -> None:
        assert _normalize("Hello WORLD") == "hello world"

    def test_strip_diacritics(self) -> None:
        assert _normalize("ąęśćżźółń") == "aesczzoln"

    def test_strip_punctuation(self) -> None:
        assert _normalize("Hello, world!") == "hello world"

    def test_collapse_whitespace(self) -> None:
        assert _normalize("  too   many   spaces  ") == "too many spaces"

    def test_polish_l(self) -> None:
        assert _strip_diacritics("łódź") == "lodz"


# --- Test: Keyword Matching ---


class TestKeywordMatch:
    def test_polish_new_opportunity(self, sample_workflows) -> None:
        intent = _keyword_match("Mam nowe opportunity, Siemens, WMS", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "new_opportunity"
        assert intent.source == "keyword"
        assert intent.confidence > 0

    def test_english_attention(self, sample_workflows) -> None:
        intent = _keyword_match("what needs attention", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "attention_scan"

    def test_polish_my_tasks(self, sample_workflows) -> None:
        intent = _keyword_match("co mam do zrobienia?", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "my_tasks"

    def test_polish_prep_deck(self, sample_workflows) -> None:
        intent = _keyword_match("przygotuj prezentację demo", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "prep_deck"

    def test_polish_brief(self, sample_workflows) -> None:
        intent = _keyword_match("brief na Lenzing", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "project_brief"

    def test_polish_archive(self, sample_workflows) -> None:
        intent = _keyword_match("archiwizuj Honda, przegrany", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "archive_project"

    def test_no_match_chitchat(self, sample_workflows) -> None:
        intent = _keyword_match("jaka jest pogoda", sample_workflows)
        assert intent is None

    def test_longest_phrase_wins(self, sample_workflows) -> None:
        # "co wymaga uwagi" is longer than just "status"
        intent = _keyword_match("co wymaga uwagi dzisiaj", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "attention_scan"

    def test_english_new_client(self, sample_workflows) -> None:
        intent = _keyword_match("new client Bosch WMS", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "new_opportunity"

    def test_add_task_explicit(self, sample_workflows) -> None:
        intent = _keyword_match("dodaj task prepare deck", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "add_task"


# --- Test: Task Shortcuts ---


class TestTaskShortcuts:
    def test_musze_trigger(self, sample_workflows) -> None:
        intent = _keyword_match("muszę przygotować brief na Lenzing", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "add_task"
        assert "title" in intent.parameters

    def test_need_to_trigger(self, sample_workflows) -> None:
        intent = _keyword_match("I need to review the Lenzing proposal", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "add_task"

    def test_zaplanuj_trigger(self, sample_workflows) -> None:
        intent = _keyword_match("zaplanuj spotkanie z klientem", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "add_task"

    def test_do_piatku_extracts_deadline_and_strips_title(self, sample_workflows) -> None:
        intent = _keyword_match("muszę zrobić brief do piątku", sample_workflows)
        assert intent is not None
        assert intent.workflow_id == "add_task"
        assert "deadline" in intent.parameters
        parsed = date.fromisoformat(intent.parameters["deadline"])
        assert parsed.weekday() == 4  # Friday
        # "do piątku" should not remain in title
        assert "piatku" not in intent.parameters["title"].lower()
        assert "piątku" not in intent.parameters["title"].lower()

    def test_do_srody_extracts_deadline_and_strips_title(self, sample_workflows) -> None:
        intent = _keyword_match("muszę wysłać ofertę do środy", sample_workflows)
        assert intent is not None
        assert "deadline" in intent.parameters
        parsed = date.fromisoformat(intent.parameters["deadline"])
        assert parsed.weekday() == 2  # Wednesday
        assert "srody" not in intent.parameters["title"].lower()
        assert "środy" not in intent.parameters["title"].lower()

    def test_by_friday_strips_from_title(self, sample_workflows) -> None:
        intent = _keyword_match("I need to send proposal by friday", sample_workflows)
        assert intent is not None
        assert "deadline" in intent.parameters
        assert "friday" not in intent.parameters["title"].lower()


# --- Test: Parameter Extraction ---


class TestExtractDate:
    def test_iso_date(self) -> None:
        assert _extract_date("spotkanie 2026-03-15") == "2026-03-15"

    def test_polish_month(self) -> None:
        result = _extract_date("15 marca")
        assert result is not None
        assert result.endswith("-03-15")

    def test_jutro(self) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert _extract_date("jutro") == tomorrow

    def test_tomorrow(self) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        assert _extract_date("tomorrow") == tomorrow

    def test_piatek(self) -> None:
        result = _extract_date("w piatek")
        assert result is not None
        parsed = date.fromisoformat(result)
        assert parsed.weekday() == 4  # Friday

    def test_friday(self) -> None:
        result = _extract_date("on friday")
        assert result is not None
        parsed = date.fromisoformat(result)
        assert parsed.weekday() == 4

    def test_no_date(self) -> None:
        assert _extract_date("hello world") is None


class TestExtractProduct:
    def test_wms(self) -> None:
        assert _extract_product("need wms solution") == "WMS"

    def test_ibp_alias(self) -> None:
        assert _extract_product("ibp implementation") == "Planning"

    def test_no_product(self) -> None:
        assert _extract_product("hello world") is None


class TestExtractPriority:
    def test_pilne(self) -> None:
        assert _extract_priority("to jest pilne") == "high"

    def test_urgent(self) -> None:
        assert _extract_priority("urgent task") == "high"

    def test_no_priority(self) -> None:
        assert _extract_priority("normal task") is None


class TestExtractReason:
    def test_won(self) -> None:
        assert _extract_reason("wygrana") == "won"

    def test_lost(self) -> None:
        assert _extract_reason("przegrana") == "lost"

    def test_cancelled(self) -> None:
        assert _extract_reason("anulowane") == "cancelled"


class TestNextWeekday:
    def test_next_friday_from_monday(self) -> None:
        monday = date(2026, 3, 9)  # Monday
        friday = _next_weekday(monday, 4)
        assert friday == date(2026, 3, 13)
        assert friday.weekday() == 4

    def test_next_monday_from_friday(self) -> None:
        friday = date(2026, 3, 13)
        monday = _next_weekday(friday, 0)
        assert monday == date(2026, 3, 16)

    def test_same_day_goes_to_next_week(self) -> None:
        monday = date(2026, 3, 9)
        next_mon = _next_weekday(monday, 0)  # Monday asking for Monday
        assert next_mon == date(2026, 3, 16)


# --- Test: Route (integration) ---


class TestRoute:
    def test_keyword_route(self, sample_workflows) -> None:
        intent = route("co wymaga uwagi?", sample_workflows, use_llm=False)
        assert intent.workflow_id == "attention_scan"
        assert intent.source == "keyword"

    def test_no_match_no_llm(self, sample_workflows) -> None:
        intent = route("jaka jest pogoda", sample_workflows, use_llm=False)
        assert intent.workflow_id is None
        assert intent.source == "none"
        assert intent.response_text is not None

    def test_no_match_llm_disabled(self, sample_workflows) -> None:
        intent = route("something vague", sample_workflows, use_llm=False)
        assert intent.workflow_id is None
