"""Workflow execution engine.

Reads workflow definitions from workflows.yaml.
Executes steps sequentially: agent calls, vault writes, python functions.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from corp_by_os.config import get_config
from corp_by_os.models import (
    StepResult,
    Workflow,
    WorkflowParam,
    WorkflowResult,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


# --- Loading ---


def load_workflows(yaml_path: Path | None = None) -> dict[str, Workflow]:
    """Load workflow definitions from YAML file.

    Args:
        yaml_path: Path to workflows.yaml. Defaults to config/workflows.yaml in repo.

    Returns:
        Dict mapping workflow_id -> Workflow.
    """
    if yaml_path is None:
        cfg = get_config()
        yaml_path = cfg.repo_path / "config" / "workflows.yaml"

    if not yaml_path.exists():
        logger.warning("Workflows file not found: %s", yaml_path)
        return {}

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "workflows" not in data:
        return {}

    workflows: dict[str, Workflow] = {}
    for wf_id, wf_data in data["workflows"].items():
        workflows[wf_id] = _parse_workflow(wf_id, wf_data)

    return workflows


def _parse_workflow(wf_id: str, data: dict[str, Any]) -> Workflow:
    """Parse a single workflow definition from YAML data."""
    # Parse parameters
    params: dict[str, WorkflowParam] = {}
    for param_name, param_data in (data.get("parameters") or {}).items():
        if isinstance(param_data, dict):
            params[param_name] = WorkflowParam(
                type=param_data.get("type", "string"),
                required=param_data.get("required", True),
                default=param_data.get("default"),
            )
        else:
            params[param_name] = WorkflowParam(type="string")

    # Parse steps
    steps: list[WorkflowStep] = []
    for step_data in data.get("steps", []):
        step_type = _infer_step_type(step_data)
        steps.append(WorkflowStep(
            type=step_type,
            description=step_data.get("description", ""),
            agent=step_data.get("agent"),
            command=step_data.get("command"),
            conditional_args=step_data.get("conditional_args"),
            action=step_data.get("action"),
            params=step_data.get("params", {}),
        ))

    return Workflow(
        id=wf_id,
        description=data.get("description", ""),
        trigger_phrases=data.get("trigger_phrases", []),
        parameters=params,
        steps=steps,
        confirmation=data.get("confirmation", False),
        cost_estimate=data.get("cost_estimate"),
    )


def _infer_step_type(step_data: dict[str, Any]) -> str:
    """Infer step type from its fields."""
    if step_data.get("agent"):
        return "agent"
    if step_data.get("action"):
        return "python"
    return "python"


# --- Execution ---


def execute_workflow(
    workflow: Workflow,
    params: dict[str, str],
    dry_run: bool = False,
) -> WorkflowResult:
    """Execute a workflow with given parameters.

    Args:
        workflow: Workflow definition to execute.
        params: User-provided parameter values.
        dry_run: If True, preview steps without executing.

    Returns:
        WorkflowResult with step outcomes.
    """
    start = time.time()
    step_results: list[StepResult] = []

    # Validate required params
    for param_name, param_def in workflow.parameters.items():
        if param_def.required and param_name not in params:
            if param_def.default is not None:
                params[param_name] = param_def.default
            else:
                return WorkflowResult(
                    workflow_id=workflow.id,
                    success=False,
                    steps=[StepResult(
                        step_index=0,
                        description="Parameter validation",
                        success=False,
                        error=f"Missing required parameter: {param_name}",
                    )],
                    duration_seconds=time.time() - start,
                )

    # Apply defaults for optional params
    for param_name, param_def in workflow.parameters.items():
        if param_name not in params and param_def.default is not None:
            params[param_name] = param_def.default

    for i, step in enumerate(workflow.steps):
        if dry_run:
            step_results.append(StepResult(
                step_index=i,
                description=step.description,
                success=True,
                output="[dry-run] Would execute",
            ))
            continue

        step_start = time.time()
        try:
            if step.type == "agent":
                result = _execute_agent_step(step, params)
            elif step.type == "python":
                result = _execute_python_step(step, params)
            else:
                result = StepResult(
                    step_index=i,
                    description=step.description,
                    success=False,
                    error=f"Unknown step type: {step.type}",
                )
        except Exception as e:
            logger.exception("Step %d failed: %s", i, e)
            result = StepResult(
                step_index=i,
                description=step.description,
                success=False,
                error=str(e),
            )

        result.step_index = i
        result.duration_seconds = time.time() - step_start
        step_results.append(result)

        if not result.success:
            logger.error("Step %d failed, stopping workflow: %s", i, result.error)
            break

    return WorkflowResult(
        workflow_id=workflow.id,
        success=all(s.success for s in step_results),
        steps=step_results,
        duration_seconds=time.time() - start,
    )


def preview_workflow(workflow: Workflow, params: dict[str, str]) -> str:
    """Generate a human-readable preview of what a workflow would do."""
    lines = [
        f"Workflow: {workflow.id}",
        f"Description: {workflow.description}",
    ]

    if workflow.cost_estimate:
        lines.append(f"Cost estimate: {workflow.cost_estimate}")

    lines.append("")
    lines.append("Steps:")
    for i, step in enumerate(workflow.steps, 1):
        desc = _interpolate(step.description, params)
        if step.type == "agent":
            cmd = _build_agent_command(step, params)
            lines.append(f"  {i}. [{step.agent}] {desc}")
            lines.append(f"     Command: {' '.join(cmd)}")
        else:
            lines.append(f"  {i}. {desc}")

    return "\n".join(lines)


# --- Step executors ---


def _execute_agent_step(step: WorkflowStep, params: dict[str, str]) -> StepResult:
    """Execute an agent step via subprocess."""
    cmd = _build_agent_command(step, params)
    desc = _interpolate(step.description, params)

    logger.info("Running agent command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            shell=False,
        )

        if result.returncode == 0:
            return StepResult(
                step_index=0,
                description=desc,
                success=True,
                output=result.stdout.strip(),
            )
        else:
            return StepResult(
                step_index=0,
                description=desc,
                success=False,
                output=result.stdout.strip(),
                error=result.stderr.strip() or f"Exit code {result.returncode}",
            )
    except subprocess.TimeoutExpired:
        return StepResult(
            step_index=0,
            description=desc,
            success=False,
            error="Command timed out after 300s",
        )
    except FileNotFoundError as e:
        return StepResult(
            step_index=0,
            description=desc,
            success=False,
            error=f"Command not found: {e}",
        )


def _execute_python_step(step: WorkflowStep, params: dict[str, str]) -> StepResult:
    """Execute a built-in Python action."""
    from corp_by_os.built_in_actions import get_action

    action_name = step.action
    if not action_name:
        return StepResult(
            step_index=0,
            description=step.description,
            success=False,
            error="No action specified for python step",
        )

    action_fn = get_action(action_name)
    if action_fn is None:
        return StepResult(
            step_index=0,
            description=step.description,
            success=False,
            error=f"Unknown action: {action_name}",
        )

    desc = _interpolate(step.description, params)
    # Merge step.params with workflow params
    merged_params = {**params, **step.params}
    return action_fn(merged_params)


# --- Helpers ---


def _build_agent_command(step: WorkflowStep, params: dict[str, str]) -> list[str]:
    """Build the full CLI command for an agent step."""
    if step.command:
        cmd = [_interpolate(part, params) for part in step.command]
    else:
        # Fallback: use agent CLI name from agents.yaml
        cfg = get_config()
        agent_info = cfg.agents.get(step.agent or "", {})
        cli = agent_info.get("cli", step.agent or "")
        cmd = [cli]

    # Add conditional args
    if step.conditional_args:
        for param_name, extra_args in step.conditional_args.items():
            if param_name in params and params[param_name]:
                cmd.extend([_interpolate(a, params) for a in extra_args])

    return cmd


def _interpolate(template: str, params: dict[str, str]) -> str:
    """Replace {param_name} placeholders in a string."""
    result = template
    for key, value in params.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result
