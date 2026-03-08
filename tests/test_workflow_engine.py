"""Tests for workflow_engine module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from corp_by_os.models import (
    StepResult,
    Workflow,
    WorkflowParam,
    WorkflowResult,
    WorkflowStep,
)
from corp_by_os.workflow_engine import (
    _build_agent_command,
    _interpolate,
    _parse_workflow,
    execute_workflow,
    load_workflows,
    preview_workflow,
)


# --- Fixtures ---


@pytest.fixture()
def workflows_yaml(tmp_path: Path) -> Path:
    """Create a temporary workflows.yaml."""
    data = {
        "workflows": {
            "test_workflow": {
                "description": "Test workflow",
                "trigger_phrases": ["test"],
                "parameters": {
                    "name": {"type": "string", "required": True},
                    "optional_arg": {"type": "string", "required": False, "default": "default_val"},
                },
                "steps": [
                    {
                        "type": "agent",
                        "description": "Run agent step",
                        "agent": "test-agent",
                        "command": ["test-cli", "run", "{name}"],
                        "conditional_args": {
                            "optional_arg": ["--opt", "{optional_arg}"],
                        },
                    },
                    {
                        "type": "python",
                        "description": "Run python step",
                        "action": "test_action",
                    },
                ],
                "confirmation": True,
                "cost_estimate": "$0.10",
            },
            "simple_workflow": {
                "description": "Simple no-params workflow",
                "parameters": {},
                "steps": [
                    {
                        "type": "python",
                        "description": "Simple action",
                        "action": "scan_attention",
                    },
                ],
                "confirmation": False,
            },
        },
    }
    yaml_path = tmp_path / "workflows.yaml"
    yaml_path.write_text(yaml.dump(data), encoding="utf-8")
    return yaml_path


@pytest.fixture()
def sample_workflow() -> Workflow:
    """Create a sample workflow for testing."""
    return Workflow(
        id="test_wf",
        description="Test workflow",
        parameters={
            "name": WorkflowParam(type="string", required=True),
        },
        steps=[
            WorkflowStep(
                type="agent",
                description="Agent step",
                agent="test-agent",
                command=["test-cli", "run", "{name}"],
            ),
            WorkflowStep(
                type="python",
                description="Python step",
                action="test_action",
            ),
        ],
        confirmation=True,
        cost_estimate="$0.10",
    )


# --- Test: Loading ---


class TestLoadWorkflows:
    def test_load_from_yaml(self, workflows_yaml: Path) -> None:
        workflows = load_workflows(workflows_yaml)
        assert len(workflows) == 2
        assert "test_workflow" in workflows
        assert "simple_workflow" in workflows

    def test_load_workflow_fields(self, workflows_yaml: Path) -> None:
        workflows = load_workflows(workflows_yaml)
        wf = workflows["test_workflow"]
        assert wf.id == "test_workflow"
        assert wf.description == "Test workflow"
        assert wf.confirmation is True
        assert wf.cost_estimate == "$0.10"
        assert len(wf.steps) == 2
        assert len(wf.parameters) == 2

    def test_load_parameters(self, workflows_yaml: Path) -> None:
        workflows = load_workflows(workflows_yaml)
        wf = workflows["test_workflow"]
        assert "name" in wf.parameters
        assert wf.parameters["name"].required is True
        assert wf.parameters["optional_arg"].required is False
        assert wf.parameters["optional_arg"].default == "default_val"

    def test_load_steps(self, workflows_yaml: Path) -> None:
        workflows = load_workflows(workflows_yaml)
        wf = workflows["test_workflow"]
        agent_step = wf.steps[0]
        assert agent_step.type == "agent"
        assert agent_step.agent == "test-agent"
        assert agent_step.command == ["test-cli", "run", "{name}"]

        python_step = wf.steps[1]
        assert python_step.type == "python"
        assert python_step.action == "test_action"

    def test_load_missing_file(self, tmp_path: Path) -> None:
        workflows = load_workflows(tmp_path / "nonexistent.yaml")
        assert workflows == {}

    def test_load_empty_file(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("", encoding="utf-8")
        workflows = load_workflows(empty)
        assert workflows == {}

    def test_load_conditional_args(self, workflows_yaml: Path) -> None:
        workflows = load_workflows(workflows_yaml)
        wf = workflows["test_workflow"]
        step = wf.steps[0]
        assert step.conditional_args == {"optional_arg": ["--opt", "{optional_arg}"]}


# --- Test: Execution ---


class TestExecuteWorkflow:
    def test_dry_run(self, sample_workflow: Workflow) -> None:
        result = execute_workflow(sample_workflow, {"name": "test"}, dry_run=True)
        assert result.success is True
        assert len(result.steps) == 2
        assert all("[dry-run]" in s.output for s in result.steps)

    def test_missing_required_param(self, sample_workflow: Workflow) -> None:
        result = execute_workflow(sample_workflow, {})
        assert result.success is False
        assert "Missing required parameter" in result.steps[0].error

    def test_default_params_applied(self) -> None:
        wf = Workflow(
            id="test",
            description="Test",
            parameters={
                "x": WorkflowParam(type="string", required=False, default="hello"),
            },
            steps=[
                WorkflowStep(type="python", description="Step", action="scan_attention"),
            ],
        )
        # Should not fail — default should be applied
        with patch("corp_by_os.workflow_engine._execute_python_step") as mock_exec:
            mock_exec.return_value = StepResult(
                step_index=0, description="Step", success=True,
            )
            result = execute_workflow(wf, {})
            assert result.success is True

    @patch("corp_by_os.workflow_engine._execute_agent_step")
    @patch("corp_by_os.workflow_engine._execute_python_step")
    def test_sequential_execution(self, mock_python, mock_agent, sample_workflow: Workflow) -> None:
        mock_agent.return_value = StepResult(step_index=0, description="Agent", success=True)
        mock_python.return_value = StepResult(step_index=1, description="Python", success=True)

        result = execute_workflow(sample_workflow, {"name": "test"})
        assert result.success is True
        assert len(result.steps) == 2
        mock_agent.assert_called_once()
        mock_python.assert_called_once()

    @patch("corp_by_os.workflow_engine._execute_agent_step")
    @patch("corp_by_os.workflow_engine._execute_python_step")
    def test_stops_on_failure(self, mock_python, mock_agent, sample_workflow: Workflow) -> None:
        mock_agent.return_value = StepResult(
            step_index=0, description="Agent", success=False, error="Agent failed",
        )

        result = execute_workflow(sample_workflow, {"name": "test"})
        assert result.success is False
        assert len(result.steps) == 1  # stopped after first failure
        mock_python.assert_not_called()

    @patch("corp_by_os.workflow_engine._execute_agent_step")
    def test_exception_in_step(self, mock_agent, sample_workflow: Workflow) -> None:
        mock_agent.side_effect = RuntimeError("boom")

        result = execute_workflow(sample_workflow, {"name": "test"})
        assert result.success is False
        assert "boom" in result.steps[0].error

    def test_duration_tracked(self, sample_workflow: Workflow) -> None:
        result = execute_workflow(sample_workflow, {"name": "test"}, dry_run=True)
        assert result.duration_seconds >= 0


# --- Test: Agent step ---


class TestAgentStep:
    @patch("subprocess.run")
    def test_successful_agent_step(self, mock_run) -> None:
        from corp_by_os.workflow_engine import _execute_agent_step

        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        step = WorkflowStep(
            type="agent", description="Test", agent="test",
            command=["test-cli", "do", "{name}"],
        )
        result = _execute_agent_step(step, {"name": "foo"})
        assert result.success is True
        assert result.output == "OK"
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_failed_agent_step(self, mock_run) -> None:
        from corp_by_os.workflow_engine import _execute_agent_step

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error occurred")
        step = WorkflowStep(
            type="agent", description="Test", agent="test",
            command=["test-cli", "do"],
        )
        result = _execute_agent_step(step, {})
        assert result.success is False
        assert "Error occurred" in result.error

    @patch("subprocess.run")
    def test_command_not_found(self, mock_run) -> None:
        from corp_by_os.workflow_engine import _execute_agent_step

        mock_run.side_effect = FileNotFoundError("not found")
        step = WorkflowStep(
            type="agent", description="Test", agent="test",
            command=["nonexistent-cli"],
        )
        result = _execute_agent_step(step, {})
        assert result.success is False
        assert "not found" in result.error

    @patch("subprocess.run")
    def test_timeout(self, mock_run) -> None:
        import subprocess as sp
        from corp_by_os.workflow_engine import _execute_agent_step

        mock_run.side_effect = sp.TimeoutExpired(cmd="test", timeout=300)
        step = WorkflowStep(
            type="agent", description="Test", agent="test",
            command=["slow-cli"],
        )
        result = _execute_agent_step(step, {})
        assert result.success is False
        assert "timed out" in result.error


# --- Test: Python step ---


class TestPythonStep:
    def test_unknown_action(self) -> None:
        from corp_by_os.workflow_engine import _execute_python_step

        step = WorkflowStep(
            type="python", description="Test",
            action="nonexistent_action",
        )
        result = _execute_python_step(step, {})
        assert result.success is False
        assert "Unknown action" in result.error

    def test_no_action_specified(self) -> None:
        from corp_by_os.workflow_engine import _execute_python_step

        step = WorkflowStep(type="python", description="Test")
        result = _execute_python_step(step, {})
        assert result.success is False
        assert "No action specified" in result.error

    @patch("corp_by_os.built_in_actions.get_action")
    def test_action_dispatched(self, mock_get) -> None:
        from corp_by_os.workflow_engine import _execute_python_step

        mock_fn = MagicMock(return_value=StepResult(
            step_index=0, description="Test", success=True, output="Done",
        ))
        mock_get.return_value = mock_fn

        step = WorkflowStep(type="python", description="Test", action="my_action")
        result = _execute_python_step(step, {"key": "value"})
        assert result.success is True
        mock_fn.assert_called_once_with({"key": "value"})


# --- Test: Helpers ---


class TestHelpers:
    def test_interpolate(self) -> None:
        assert _interpolate("Hello {name}!", {"name": "World"}) == "Hello World!"

    def test_interpolate_multiple(self) -> None:
        result = _interpolate("{a} and {b}", {"a": "X", "b": "Y"})
        assert result == "X and Y"

    def test_interpolate_missing_key(self) -> None:
        result = _interpolate("Hello {unknown}!", {"name": "World"})
        assert result == "Hello {unknown}!"

    def test_build_agent_command(self) -> None:
        step = WorkflowStep(
            type="agent", description="Test",
            command=["cli", "run", "{name}"],
        )
        cmd = _build_agent_command(step, {"name": "foo"})
        assert cmd == ["cli", "run", "foo"]

    def test_build_agent_command_with_conditional(self) -> None:
        step = WorkflowStep(
            type="agent", description="Test",
            command=["cli", "run"],
            conditional_args={"opt": ["--opt", "{opt}"]},
        )
        cmd = _build_agent_command(step, {"opt": "bar"})
        assert cmd == ["cli", "run", "--opt", "bar"]

    def test_build_agent_command_conditional_missing(self) -> None:
        step = WorkflowStep(
            type="agent", description="Test",
            command=["cli", "run"],
            conditional_args={"opt": ["--opt", "{opt}"]},
        )
        cmd = _build_agent_command(step, {})
        assert cmd == ["cli", "run"]


# --- Test: Preview ---


class TestPreview:
    def test_preview_output(self, sample_workflow: Workflow) -> None:
        output = preview_workflow(sample_workflow, {"name": "test"})
        assert "test_wf" in output
        assert "Test workflow" in output
        assert "Agent step" in output
        assert "Python step" in output

    def test_preview_with_cost(self, sample_workflow: Workflow) -> None:
        output = preview_workflow(sample_workflow, {"name": "test"})
        assert "$0.10" in output
