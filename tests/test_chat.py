"""Tests for chat module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from corp_by_os.chat import (
    QUIT_COMMANDS,
    _handle_special_command,
    _show_help,
    _show_status,
)
from corp_by_os.models import Workflow, WorkflowParam


# --- Fixtures ---


@pytest.fixture()
def sample_workflows() -> dict[str, Workflow]:
    return {
        "attention_scan": Workflow(
            id="attention_scan",
            description="Scan all projects",
            trigger_phrases=["what needs attention"],
            parameters={},
        ),
        "my_tasks": Workflow(
            id="my_tasks",
            description="Show tasks",
            trigger_phrases=["my tasks"],
            parameters={},
        ),
    }


# --- Test: Quit Commands ---


class TestQuitCommands:
    def test_quit_recognized(self) -> None:
        assert "quit" in QUIT_COMMANDS
        assert "exit" in QUIT_COMMANDS
        assert "q" in QUIT_COMMANDS

    def test_non_quit(self) -> None:
        assert "hello" not in QUIT_COMMANDS


# --- Test: Special Commands ---


class TestSpecialCommands:
    def test_help_command(self, sample_workflows) -> None:
        assert _handle_special_command("help", sample_workflows) is True

    def test_status_command(self, sample_workflows) -> None:
        with patch("corp_by_os.chat._show_status"):
            assert _handle_special_command("status", sample_workflows) is True

    def test_direct_command(self, sample_workflows) -> None:
        with patch("corp_by_os.chat._run_direct_command") as mock_run:
            assert _handle_special_command("!project list", sample_workflows) is True
            mock_run.assert_called_once_with("project list")

    def test_normal_input_not_special(self, sample_workflows) -> None:
        assert _handle_special_command("nowe opportunity", sample_workflows) is False

    def test_case_insensitive_help(self, sample_workflows) -> None:
        assert _handle_special_command("HELP", sample_workflows) is True


# --- Test: Chat Loop ---


class TestChatLoop:
    @patch("corp_by_os.chat.console")
    def test_quit_exits_loop(self, mock_console) -> None:
        from corp_by_os.chat import chat_loop

        mock_console.input.return_value = "quit"
        chat_loop(use_llm=False)
        # Should exit without error

    @patch("corp_by_os.chat.console")
    def test_empty_input_continues(self, mock_console) -> None:
        from corp_by_os.chat import chat_loop

        mock_console.input.side_effect = ["", "quit"]
        chat_loop(use_llm=False)

    @patch("corp_by_os.chat.console")
    def test_eof_exits(self, mock_console) -> None:
        from corp_by_os.chat import chat_loop

        mock_console.input.side_effect = EOFError()
        chat_loop(use_llm=False)

    @patch("corp_by_os.chat.console")
    def test_keyboard_interrupt_exits(self, mock_console) -> None:
        from corp_by_os.chat import chat_loop

        mock_console.input.side_effect = KeyboardInterrupt()
        chat_loop(use_llm=False)

    @patch("corp_by_os.chat.console")
    @patch("corp_by_os.chat.route")
    @patch("corp_by_os.chat.execute_workflow")
    @patch("corp_by_os.chat.load_workflows")
    def test_workflow_execution(self, mock_load, mock_exec, mock_route, mock_console) -> None:
        from corp_by_os.chat import chat_loop
        from corp_by_os.intent_router import Intent
        from corp_by_os.models import WorkflowResult, StepResult

        mock_load.return_value = {
            "attention_scan": Workflow(
                id="attention_scan",
                description="Scan projects",
                trigger_phrases=["what needs attention"],
                parameters={},
                confirmation=False,
            ),
        }
        mock_route.return_value = Intent(
            workflow_id="attention_scan",
            parameters={},
            confidence=0.9,
            source="keyword",
        )
        mock_exec.return_value = WorkflowResult(
            workflow_id="attention_scan",
            success=True,
            steps=[StepResult(step_index=0, description="Scan", success=True)],
            duration_seconds=1.0,
        )

        mock_console.input.side_effect = ["what needs attention", "quit"]
        chat_loop(use_llm=False)

        mock_exec.assert_called_once()
