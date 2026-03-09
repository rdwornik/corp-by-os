"""CLI entry point for corp-by-os.

Commands:
    corp project list [--status active]
    corp project show <name>
    corp project open <name>
    corp vault validate [project]
    corp doctor
    corp run <workflow> [PARAMS]
    corp run --list
    corp task add "Title" [--project X] [--deadline DATE] [--priority high]
    corp task list [--status todo] [--project X]
    corp task done "Title"
    corp tasks
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Use ASCII-safe markers for Windows legacy console compatibility
CHECK = "Y"
DASH = "-"

from corp_by_os.config import get_config
from corp_by_os.project_resolver import resolve_project
from corp_by_os.vault_io import list_projects, read_project_info, validate_vault

console = Console()
logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def cli(verbose: bool) -> None:
    """Corp-by-os — root orchestrator for the agent ecosystem."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


# --- Project commands ---


@cli.group()
def project() -> None:
    """Manage projects."""


@project.command("list")
@click.option("--status", "-s", default=None, help="Filter by status (active, rfp, won, etc.)")
def project_list(status: str | None) -> None:
    """List all projects with metadata status."""
    projects = list_projects(status_filter=status)

    if not projects:
        console.print("[yellow]No projects found.[/yellow]")
        return

    table = Table(title="Projects", show_lines=False)
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Client", style="white")
    table.add_column("Status", style="green")
    table.add_column("Vault", justify="center")
    table.add_column("OneDrive", justify="center")
    table.add_column("Facts", justify="right")

    for p in projects:
        table.add_row(
            p.project_id,
            p.client,
            p.status,
            CHECK if p.has_vault else DASH,
            CHECK if p.has_onedrive else DASH,
            str(p.facts_count) if p.facts_count else DASH,
        )

    console.print(table)
    console.print(f"\n[dim]{len(projects)} projects total[/dim]")


@project.command("show")
@click.argument("name")
def project_show(name: str) -> None:
    """Show project details (fuzzy name match)."""
    resolved = resolve_project(name)

    if not resolved:
        console.print(f"[red]No project matching '{name}' found.[/red]")
        sys.exit(1)

    if resolved.score < 1.0:
        console.print(f"[dim]Matched: {resolved.folder_name} (score: {resolved.score:.1f})[/dim]")

    # Try to read project-info.yaml
    info = read_project_info(resolved.project_id)

    if info:
        table = Table(title=f"Project: {info.client}", show_header=False, box=None)
        table.add_column("Field", style="cyan", width=18)
        table.add_column("Value", style="white")

        table.add_row("Project ID", info.project_id)
        table.add_row("Client", info.client)
        table.add_row("Status", info.status)
        table.add_row("Products", ", ".join(info.products) if info.products else "–")
        table.add_row("Topics", ", ".join(info.topics) if info.topics else "–")
        table.add_row("Domains", ", ".join(info.domains) if info.domains else "–")
        table.add_row("Files Processed", str(info.files_processed))
        table.add_row("Facts", str(info.facts_count))
        table.add_row("Last Extracted", info.last_extracted or "–")

        if info.region:
            table.add_row("Region", info.region)
        if info.industry:
            table.add_row("Industry", info.industry)
        if info.people:
            table.add_row("People", ", ".join(info.people))

        console.print(table)
    else:
        console.print(f"[yellow]No project-info.yaml found in vault for {resolved.folder_name}[/yellow]")

    console.print()
    if resolved.onedrive_path:
        console.print(f"[dim]OneDrive:[/dim] {resolved.onedrive_path}")
    if resolved.vault_path:
        console.print(f"[dim]Vault:[/dim]    {resolved.vault_path}")


@project.command("open")
@click.argument("name")
def project_open(name: str) -> None:
    """Open project folder in Explorer (fuzzy name match)."""
    resolved = resolve_project(name)

    if not resolved:
        console.print(f"[red]No project matching '{name}' found.[/red]")
        sys.exit(1)

    path = resolved.onedrive_path or resolved.vault_path
    if not path:
        console.print(f"[red]No folder found for {resolved.folder_name}[/red]")
        sys.exit(1)

    console.print(f"Opening {path}")
    os.startfile(str(path))


# --- Vault commands ---


@cli.group()
def vault() -> None:
    """Vault operations."""


@vault.command("validate")
@click.argument("project", required=False, default=None)
def vault_validate(project: str | None) -> None:
    """Validate vault structure and frontmatter."""
    project_id = None
    if project:
        resolved = resolve_project(project)
        if resolved:
            project_id = resolved.project_id
        else:
            console.print(f"[red]No project matching '{project}' found.[/red]")
            sys.exit(1)

    console.print("[dim]Running validation...[/dim]")
    report = validate_vault(project_id=project_id)

    if report.is_valid and not report.issues:
        console.print(f"[green]OK[/green] -- {report.notes_checked} notes checked, {report.notes_valid} valid")
        return

    # Show issues
    table = Table(title="Validation Issues", show_lines=False)
    table.add_column("Level", style="bold", width=8)
    table.add_column("Path", style="dim")
    table.add_column("Issue", style="white")

    for issue in report.issues:
        level_style = "red" if issue.level == "error" else "yellow"
        table.add_row(
            f"[{level_style}]{issue.level}[/{level_style}]",
            str(issue.path.name),
            issue.message,
        )

    console.print(table)
    console.print(f"\n[dim]{report.notes_checked} notes checked, {report.notes_valid} valid, {len(report.issues)} issues[/dim]")


# --- Doctor ---


@cli.command()
def doctor() -> None:
    """Check all agent CLIs are on PATH and working."""
    cfg = get_config()

    table = Table(title="Agent Health Check", show_lines=False)
    table.add_column("Agent", style="cyan", width=28)
    table.add_column("CLI", style="white", width=20)
    table.add_column("Status", width=10)
    table.add_column("Detail", style="dim")

    for name, agent in cfg.agents.items():
        cli_cmd = agent.get("cli", "")
        status_flag = agent.get("status", "")

        if status_flag == "legacy":
            table.add_row(name, cli_cmd, "[yellow]legacy[/yellow]", "Needs rewire")
            continue

        if not cli_cmd or cli_cmd == "TBD":
            table.add_row(name, cli_cmd or "–", "[yellow]TBD[/yellow]", "CLI not configured")
            continue

        # Check if CLI is available
        # Handle compound commands like "python -m src.cli"
        check_cmd = cli_cmd.split()[0]
        try:
            result = subprocess.run(
                [check_cmd, "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if result.returncode == 0:
                table.add_row(name, cli_cmd, "[green]OK[/green]", "")
            else:
                table.add_row(name, cli_cmd, "[red]FAIL[/red]", f"exit code {result.returncode}")
        except FileNotFoundError:
            table.add_row(name, cli_cmd, "[red]NOT FOUND[/red]", "Not on PATH")
        except subprocess.TimeoutExpired:
            table.add_row(name, cli_cmd, "[yellow]TIMEOUT[/yellow]", "Took >10s")
        except Exception as e:
            table.add_row(name, cli_cmd, "[red]ERROR[/red]", str(e)[:50])

    console.print(table)


# --- Workflow commands ---


@cli.command("run")
@click.argument("workflow", required=False, default=None)
@click.option("--list", "list_workflows", is_flag=True, help="List available workflows")
@click.option("--dry-run", is_flag=True, help="Preview without executing")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
@click.option("--client", default=None, help="Client name")
@click.option("--product", default=None, help="Product name")
@click.option("--contact", default=None, help="Contact name")
@click.option("--project", default=None, help="Project name/ID")
@click.option("--topic", default=None, help="Topic/subject")
@click.option("--date", default=None, help="Date (YYYY-MM-DD)")
@click.option("--reason", default=None, help="Reason (for archive)")
@click.option("--notes", default=None, help="Additional notes")
@click.option("--title", default=None, help="Task title")
@click.option("--deadline", default=None, help="Deadline (YYYY-MM-DD)")
@click.option("--priority", default=None, help="Priority (high/medium/low)")
@click.option("--status", default=None, help="Status filter")
def run_workflow(
    workflow: str | None,
    list_workflows: bool,
    dry_run: bool,
    confirm: bool,
    **kwargs: str | None,
) -> None:
    """Execute a workflow or list available workflows."""
    from corp_by_os.workflow_engine import execute_workflow, load_workflows, preview_workflow

    workflows = load_workflows()

    if list_workflows or workflow is None:
        _show_workflow_list(workflows)
        return

    if workflow not in workflows:
        console.print(f"[red]Unknown workflow: {workflow}[/red]")
        console.print(f"[dim]Available: {', '.join(workflows.keys())}[/dim]")
        sys.exit(1)

    wf = workflows[workflow]

    # Build params from CLI options
    params = {k: v for k, v in kwargs.items() if v is not None}

    # Resolve project path if project is specified
    if "project" in params:
        resolved = resolve_project(params["project"])
        if resolved and resolved.onedrive_path:
            params["project_path"] = str(resolved.onedrive_path)

    # Preview
    if dry_run:
        preview = preview_workflow(wf, params)
        console.print(Panel(preview, title="Dry Run", border_style="yellow"))
        return

    # Show what we're about to do
    _show_workflow_panel(wf, params)

    # Confirmation
    if wf.confirmation and not confirm:
        if not click.confirm("Proceed?", default=True):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Execute
    result = execute_workflow(wf, params)

    # Show results
    for step in result.steps:
        status = "[green]OK[/green]" if step.success else "[red]FAIL[/red]"
        duration = f"({step.duration_seconds:.1f}s)" if step.duration_seconds > 0 else ""
        console.print(f"  Step {step.step_index + 1}/{len(wf.steps)}: {step.description}... {status} {duration}")
        if step.output and not step.success:
            console.print(f"    [dim]{step.output}[/dim]")
        if step.error:
            console.print(f"    [red]{step.error}[/red]")

    # Summary
    if result.success:
        console.print(Panel(
            f"All {len(result.steps)} steps succeeded in {result.duration_seconds:.1f}s",
            title="Complete",
            border_style="green",
        ))
    else:
        failed = [s for s in result.steps if not s.success]
        console.print(Panel(
            f"{len(failed)} step(s) failed. See errors above.",
            title="Failed",
            border_style="red",
        ))
        sys.exit(1)


def _show_workflow_list(workflows: dict) -> None:
    """Display available workflows in a table."""
    table = Table(title="Available Workflows", show_lines=False)
    table.add_column("Workflow", style="cyan", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Confirm", justify="center", width=8)
    table.add_column("Cost", style="dim", width=15)

    for wf_id, wf in sorted(workflows.items()):
        table.add_row(
            wf_id,
            wf.description,
            CHECK if wf.confirmation else DASH,
            wf.cost_estimate or "free",
        )

    console.print(table)


def _show_workflow_panel(wf, params: dict) -> None:
    """Show a panel with workflow details before execution."""
    lines = [f"[bold]{wf.description}[/bold]"]

    if params:
        lines.append("")
        for k, v in params.items():
            if not k.startswith("_"):
                lines.append(f"  {k}: {v}")

    if wf.cost_estimate:
        lines.append(f"\n  Cost estimate: {wf.cost_estimate}")

    lines.append(f"\n  Steps: {len(wf.steps)}")
    for i, step in enumerate(wf.steps, 1):
        tag = f"[{step.agent}]" if step.agent else ""
        lines.append(f"    {i}. {tag} {step.description}")

    console.print(Panel("\n".join(lines), title=f"Workflow: {wf.id}", border_style="blue"))


# --- Task commands ---


@cli.group("task")
def task_group() -> None:
    """Manage tasks."""


@task_group.command("add")
@click.argument("title")
@click.option("--project", "-p", default=None, help="Associated project")
@click.option("--deadline", "-d", default=None, help="Deadline (YYYY-MM-DD)")
@click.option("--priority", default="medium", type=click.Choice(["high", "medium", "low"]))
def task_add(title: str, project: str | None, deadline: str | None, priority: str) -> None:
    """Create a new task."""
    from corp_by_os.task_manager import add_task

    project_id = None
    if project:
        resolved = resolve_project(project)
        project_id = resolved.project_id if resolved else project

    path = add_task(title=title, project_id=project_id, deadline=deadline, priority=priority)
    console.print(f"[green]Created:[/green] {path.name}")


@task_group.command("list")
@click.option("--status", "-s", default="todo", help="Filter by status")
@click.option("--project", "-p", default=None, help="Filter by project")
@click.option("--all", "show_all", is_flag=True, help="Show all statuses")
def task_list(status: str, project: str | None, show_all: bool) -> None:
    """List tasks sorted by priority and deadline."""
    from corp_by_os.task_manager import list_tasks

    status_filter = None if show_all else status
    tasks = list_tasks(status_filter=status_filter, project_filter=project)

    if not tasks:
        console.print("[yellow]No tasks found.[/yellow]")
        return

    # Group by priority
    by_priority: dict[str, list] = {"high": [], "medium": [], "low": []}
    for t in tasks:
        by_priority.setdefault(t.priority.value, []).append(t)

    lines: list[str] = []
    priority_labels = {"high": "[red]HIGH[/red]", "medium": "[yellow]MEDIUM[/yellow]", "low": "[dim]LOW[/dim]"}

    for prio in ["high", "medium", "low"]:
        group = by_priority.get(prio, [])
        if not group:
            continue
        lines.append(f"\n  {priority_labels[prio]}")
        for t in group:
            deadline_str = f"  ({t.deadline})" if t.deadline else ""
            project_str = f" [dim][{t.project}][/dim]" if t.project else ""
            marker = "[green]x[/green]" if t.status.value == "done" else "[ ]"
            lines.append(f"   {marker} {t.title}{project_str}{deadline_str}")

    console.print(Panel("\n".join(lines), title="My Tasks", border_style="blue"))
    console.print(f"[dim]{len(tasks)} tasks[/dim]")


@task_group.command("done")
@click.argument("title")
def task_done(title: str) -> None:
    """Mark a task as complete (fuzzy title match)."""
    from corp_by_os.task_manager import complete_task

    if complete_task(title):
        console.print(f"[green]Completed:[/green] {title}")
    else:
        console.print(f"[red]No matching task found:[/red] {title}")
        sys.exit(1)


@cli.command("tasks")
@click.option("--status", "-s", default="todo", help="Filter by status")
@click.option("--all", "show_all", is_flag=True, help="Show all statuses")
def tasks_shortcut(status: str, show_all: bool) -> None:
    """Shortcut for 'corp task list'."""
    from corp_by_os.task_manager import list_tasks

    status_filter = None if show_all else status
    tasks = list_tasks(status_filter=status_filter)

    if not tasks:
        console.print("[yellow]No tasks found.[/yellow]")
        return

    by_priority: dict[str, list] = {"high": [], "medium": [], "low": []}
    for t in tasks:
        by_priority.setdefault(t.priority.value, []).append(t)

    lines: list[str] = []
    priority_labels = {"high": "[red]HIGH[/red]", "medium": "[yellow]MEDIUM[/yellow]", "low": "[dim]LOW[/dim]"}

    for prio in ["high", "medium", "low"]:
        group = by_priority.get(prio, [])
        if not group:
            continue
        lines.append(f"\n  {priority_labels[prio]}")
        for t in group:
            deadline_str = f"  ({t.deadline})" if t.deadline else ""
            project_str = f" [dim][{t.project}][/dim]" if t.project else ""
            lines.append(f"   [ ] {t.title}{project_str}{deadline_str}")

    console.print(Panel("\n".join(lines), title="My Tasks", border_style="blue"))
    console.print(f"[dim]{len(tasks)} tasks[/dim]")
