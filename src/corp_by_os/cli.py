"""CLI entry point for corp-by-os.

Commands:
    corp project list [--status active]
    corp project show <name>
    corp project open <name>
    corp vault validate [project]
    corp doctor
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

import click
from rich.console import Console
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
