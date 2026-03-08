"""Built-in Python actions for workflows.

These are the "python" step type handlers.
Each function takes a params dict, returns StepResult.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from corp_by_os.config import get_config
from corp_by_os.models import StepResult, VaultZone

logger = logging.getLogger(__name__)


# --- Action registry ---

_ACTIONS: dict[str, Callable[[dict[str, str]], StepResult]] = {}


def register_action(name: str) -> Callable:
    """Decorator to register a built-in action."""
    def decorator(fn: Callable[[dict[str, str]], StepResult]) -> Callable:
        _ACTIONS[name] = fn
        return fn
    return decorator


def get_action(name: str) -> Callable[[dict[str, str]], StepResult] | None:
    """Look up a registered action by name."""
    return _ACTIONS.get(name)


# --- Actions ---


@register_action("create_vault_skeleton")
def create_vault_skeleton(params: dict[str, str]) -> StepResult:
    """Create 01_projects/{project_id}/ with project-info.yaml stub."""
    cfg = get_config()
    client = params.get("client", "")
    product = params.get("product", "")

    if not client:
        return StepResult(
            step_index=0, description="Create vault skeleton",
            success=False, error="Missing 'client' parameter",
        )

    # Build project_id from client + product
    project_id = _slugify(client)
    if product:
        project_id = f"{project_id}_{_slugify(product)}"

    project_dir = cfg.vault_path / VaultZone.PROJECTS.value / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    # Create project-info.yaml stub
    info_file = project_dir / "project-info.yaml"
    if not info_file.exists():
        info = {
            "project_id": project_id,
            "client": client,
            "status": "active",
            "products": [product] if product else [],
            "topics": [],
            "domains": [],
            "files_processed": 0,
            "facts_count": 0,
            "last_extracted": None,
        }
        info_file.write_text(
            yaml.dump(info, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        logger.info("Created project-info.yaml at %s", info_file)

    # Create empty index.md
    index_file = project_dir / "index.md"
    if not index_file.exists():
        index_file.write_text(
            f"---\ntitle: {client} Project Overview\n"
            f"document_type: project_overview\n---\n\n"
            f"# {client}\n\nProject overview — auto-generated.\n",
            encoding="utf-8",
        )

    return StepResult(
        step_index=0, description="Create vault skeleton",
        success=True, output=f"Created vault skeleton at {project_dir}",
    )


@register_action("validate_project")
def validate_project(params: dict[str, str]) -> StepResult:
    """Validate a project's vault structure."""
    from corp_by_os.vault_io import validate_vault

    project = params.get("project", params.get("client", ""))
    project_id = _resolve_project_id(project, params)

    report = validate_vault(project_id=project_id)

    if report.is_valid:
        return StepResult(
            step_index=0, description="Validate project",
            success=True,
            output=f"Valid: {report.notes_checked} notes checked, {report.notes_valid} valid",
        )
    else:
        issues_str = "\n".join(f"  {i.level}: {i.message}" for i in report.issues)
        return StepResult(
            step_index=0, description="Validate project",
            success=False,
            output=f"{report.notes_checked} notes checked",
            error=f"Validation issues:\n{issues_str}",
        )


@register_action("copy_to_vault")
def copy_to_vault_action(params: dict[str, str]) -> StepResult:
    """Copy _knowledge/ and notes to vault zones."""
    from corp_by_os.vault_io import copy_to_vault

    project = params.get("project", "")
    project_id = _resolve_project_id(project, params)
    project_path = _resolve_project_path(project, params)

    if not project_path:
        return StepResult(
            step_index=0, description="Copy to vault",
            success=False, error=f"Could not resolve project path for '{project}'",
        )

    copied_total = 0

    # Copy _knowledge/ -> 01_projects/{project_id}/
    knowledge_dir = project_path / "_knowledge"
    if knowledge_dir.exists():
        copied = copy_to_vault(knowledge_dir, VaultZone.PROJECTS, project_id)
        copied_total += len(copied)

    # Copy _extracted/notes/ -> 02_sources/{project_id}/
    notes_dir = project_path / "_extracted" / "notes"
    if notes_dir.exists():
        copied = copy_to_vault(notes_dir, VaultZone.SOURCES, project_id)
        copied_total += len(copied)

    return StepResult(
        step_index=0, description="Copy to vault",
        success=True, output=f"Copied {copied_total} files to vault",
    )


@register_action("scan_attention")
def scan_attention(params: dict[str, str]) -> StepResult:
    """Scan all projects for stale/missing/incomplete items."""
    from corp_by_os.vault_io import list_projects, read_project_info

    cfg = get_config()
    projects = list_projects()
    issues: list[dict[str, str]] = []

    for proj in projects:
        pid = proj.project_id

        # Check: no vault presence
        if not proj.has_vault and proj.has_onedrive:
            issues.append({
                "project": pid,
                "severity": "MEDIUM",
                "issue": "No vault presence (exists in OneDrive only)",
            })
            continue

        # Read project info for deeper checks
        info = read_project_info(pid)

        if info is None:
            if proj.has_vault:
                issues.append({
                    "project": pid,
                    "severity": "HIGH",
                    "issue": "Missing project-info.yaml",
                })
            continue

        # Check: no extraction
        if info.facts_count == 0:
            issues.append({
                "project": pid,
                "severity": "HIGH",
                "issue": "No extraction (facts_count = 0)",
            })

        # Check: stale extraction
        if info.last_extracted:
            try:
                last = datetime.strptime(info.last_extracted, "%Y-%m-%d").date()
                if (date.today() - last) > timedelta(days=30):
                    issues.append({
                        "project": pid,
                        "severity": "MEDIUM",
                        "issue": f"Stale extraction (last: {info.last_extracted})",
                    })
            except ValueError:
                pass

        # Check: missing products
        if not info.products:
            issues.append({
                "project": pid,
                "severity": "LOW",
                "issue": "Missing products list",
            })

        # Check: missing contacts
        if not info.people:
            issues.append({
                "project": pid,
                "severity": "LOW",
                "issue": "Missing contacts/people list",
            })

    # Store issues in params for dashboard generation
    params["_attention_issues"] = _serialize_issues(issues)
    params["_attention_project_count"] = str(len(projects))

    return StepResult(
        step_index=0, description="Scan attention",
        success=True,
        output=f"Scanned {len(projects)} projects, found {len(issues)} issues",
    )


@register_action("generate_attention_dashboard")
def generate_attention_dashboard(params: dict[str, str]) -> StepResult:
    """Write 00_dashboards/attention.md with findings."""
    cfg = get_config()
    issues = _deserialize_issues(params.get("_attention_issues", "[]"))
    project_count = params.get("_attention_project_count", "0")

    # Build markdown
    lines = [
        "---",
        "title: Attention Dashboard",
        "document_type: dashboard",
        f"generated: \"{date.today().isoformat()}\"",
        "tags: [dashboard, auto-generated]",
        "---",
        "",
        "# Attention Dashboard",
        "",
        f"Scanned **{project_count}** projects on {date.today().isoformat()}.",
        "",
    ]

    if not issues:
        lines.append("All projects look healthy.")
    else:
        # Group by severity
        for severity in ["HIGH", "MEDIUM", "LOW"]:
            sev_issues = [i for i in issues if i["severity"] == severity]
            if sev_issues:
                lines.append(f"## {severity}")
                lines.append("")
                lines.append("| Project | Issue |")
                lines.append("|---|---|")
                for issue in sev_issues:
                    lines.append(f"| {issue['project']} | {issue['issue']} |")
                lines.append("")

    dashboard_path = cfg.vault_path / VaultZone.DASHBOARDS.value / "attention.md"
    dashboard_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard_path.write_text("\n".join(lines), encoding="utf-8")

    return StepResult(
        step_index=0, description="Generate attention dashboard",
        success=True, output=f"Wrote {dashboard_path} ({len(issues)} issues)",
    )


@register_action("scan_inbox")
def scan_inbox(params: dict[str, str]) -> StepResult:
    """List files in 00_Inbox/, classify by extension/name."""
    cfg = get_config()
    inbox_path = cfg.projects_root.parent / "00_Inbox"

    if not inbox_path.exists():
        return StepResult(
            step_index=0, description="Scan inbox",
            success=True, output="Inbox directory not found",
        )

    files = [f for f in inbox_path.rglob("*") if f.is_file()]

    if not files:
        return StepResult(
            step_index=0, description="Scan inbox",
            success=True, output="Inbox is empty",
        )

    classified: dict[str, list[str]] = {}
    for f in files:
        ext = f.suffix.lower() or "(no extension)"
        classified.setdefault(ext, []).append(f.name)

    output_lines = [f"Found {len(files)} files in inbox:"]
    for ext, names in sorted(classified.items()):
        output_lines.append(f"  {ext}: {len(names)} files")
        for name in names[:5]:
            output_lines.append(f"    - {name}")
        if len(names) > 5:
            output_lines.append(f"    ... and {len(names) - 5} more")

    return StepResult(
        step_index=0, description="Scan inbox",
        success=True, output="\n".join(output_lines),
    )


@register_action("generate_project_brief")
def generate_project_brief(params: dict[str, str]) -> StepResult:
    """Read facts.yaml + project-info.yaml, generate 1-pager markdown."""
    from corp_by_os.vault_io import read_project_info

    cfg = get_config()
    project = params.get("project", "")
    project_id = _resolve_project_id(project, params)

    info = read_project_info(project_id)
    if info is None:
        return StepResult(
            step_index=0, description="Generate project brief",
            success=False, error=f"No project-info.yaml found for '{project_id}'",
        )

    # Try to read facts.yaml
    facts: list[dict] = []
    project_dir = cfg.vault_path / VaultZone.PROJECTS.value / project_id
    facts_file = project_dir / "facts.yaml"
    if facts_file.exists():
        try:
            with open(facts_file, encoding="utf-8") as f:
                facts_data = yaml.safe_load(f)
            if isinstance(facts_data, list):
                facts = facts_data[:20]  # top 20 facts for brief
        except Exception:
            pass

    # Build brief
    lines = [
        "---",
        f"title: \"{info.client} — Project Brief\"",
        "document_type: brief",
        f"generated: \"{date.today().isoformat()}\"",
        "tags: [brief, auto-generated]",
        "---",
        "",
        f"# {info.client} — Project Brief",
        "",
        f"**Status:** {info.status}",
        f"**Products:** {', '.join(info.products) if info.products else 'N/A'}",
        f"**Topics:** {', '.join(info.topics) if info.topics else 'N/A'}",
        f"**Domains:** {', '.join(info.domains) if info.domains else 'N/A'}",
        f"**Files Processed:** {info.files_processed}",
        f"**Facts Extracted:** {info.facts_count}",
        f"**Last Extraction:** {info.last_extracted or 'Never'}",
        "",
    ]

    if info.people:
        lines.append("## Key People")
        lines.append("")
        for person in info.people:
            lines.append(f"- {person}")
        lines.append("")

    if facts:
        lines.append("## Key Facts")
        lines.append("")
        for fact in facts:
            if isinstance(fact, dict):
                text = fact.get("text", fact.get("fact", str(fact)))
                lines.append(f"- {text}")
            else:
                lines.append(f"- {fact}")
        lines.append("")

    brief_md = "\n".join(lines)

    # Write to vault
    brief_path = project_dir / "brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(brief_md, encoding="utf-8")

    return StepResult(
        step_index=0, description="Generate project brief",
        success=True, output=f"Brief written to {brief_path}",
    )


@register_action("archive_project")
def archive_project(params: dict[str, str]) -> StepResult:
    """Move project folder to 80_Archive/{year}/, update metadata."""
    cfg = get_config()
    project = params.get("project", "")
    reason = params.get("reason", "")
    notes = params.get("notes", "")

    if not project:
        return StepResult(
            step_index=0, description="Archive project",
            success=False, error="Missing 'project' parameter",
        )

    project_path = _resolve_project_path(project, params)
    if not project_path or not project_path.exists():
        return StepResult(
            step_index=0, description="Archive project",
            success=False, error=f"Project folder not found: {project}",
        )

    # Move to archive
    year = str(date.today().year)
    archive_dir = cfg.archive_root / year
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / project_path.name

    if dest.exists():
        return StepResult(
            step_index=0, description="Archive project",
            success=False, error=f"Archive destination already exists: {dest}",
        )

    shutil.move(str(project_path), str(dest))

    # Update project-info.yaml in vault if it exists
    project_id = _resolve_project_id(project, params)
    vault_dir = cfg.vault_path / VaultZone.PROJECTS.value / project_id
    info_file = vault_dir / "project-info.yaml"
    if info_file.exists():
        try:
            with open(info_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data["status"] = "archived"
            data["archive_reason"] = reason
            data["archive_date"] = date.today().isoformat()
            if notes:
                data["archive_notes"] = notes
            info_file.write_text(
                yaml.dump(data, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Failed to update project-info.yaml: %s", e)

    return StepResult(
        step_index=0, description="Archive project",
        success=True, output=f"Archived {project_path.name} to {dest}",
    )


@register_action("update_archive_metadata")
def update_archive_metadata(params: dict[str, str]) -> StepResult:
    """Update vault metadata after archiving."""
    project = params.get("project", "")
    project_id = _resolve_project_id(project, params)

    cfg = get_config()
    vault_dir = cfg.vault_path / VaultZone.PROJECTS.value / project_id
    info_file = vault_dir / "project-info.yaml"

    if not info_file.exists():
        return StepResult(
            step_index=0, description="Update archive metadata",
            success=True, output="No vault metadata to update",
        )

    try:
        with open(info_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data["status"] = "archived"
        info_file.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception as e:
        return StepResult(
            step_index=0, description="Update archive metadata",
            success=False, error=str(e),
        )

    return StepResult(
        step_index=0, description="Update archive metadata",
        success=True, output=f"Updated metadata for {project_id}",
    )


@register_action("add_task")
def add_task_action(params: dict[str, str]) -> StepResult:
    """Create a task note — delegates to task_manager."""
    from corp_by_os.task_manager import add_task

    title = params.get("title", "")
    if not title:
        return StepResult(
            step_index=0, description="Add task",
            success=False, error="Missing 'title' parameter",
        )

    path = add_task(
        title=title,
        project_id=params.get("project"),
        deadline=params.get("deadline"),
        priority=params.get("priority", "medium"),
    )

    return StepResult(
        step_index=0, description="Add task",
        success=True, output=f"Created task: {path.name}",
    )


@register_action("list_tasks")
def list_tasks_action(params: dict[str, str]) -> StepResult:
    """List tasks — delegates to task_manager."""
    from corp_by_os.task_manager import list_tasks

    tasks = list_tasks(
        status_filter=params.get("status", "todo"),
        project_filter=params.get("project"),
    )

    if not tasks:
        return StepResult(
            step_index=0, description="List tasks",
            success=True, output="No tasks found",
        )

    lines = [f"Found {len(tasks)} tasks:"]
    for t in tasks:
        deadline_str = f" (due: {t.deadline})" if t.deadline else ""
        project_str = f" [{t.project}]" if t.project else ""
        lines.append(f"  [{t.priority.value.upper()}] {t.title}{project_str}{deadline_str}")

    return StepResult(
        step_index=0, description="List tasks",
        success=True, output="\n".join(lines),
    )


# --- Helpers ---


def _slugify(text: str) -> str:
    """Convert text to a project slug."""
    return text.lower().replace(" ", "_").replace("-", "_")


def _resolve_project_id(project: str, params: dict[str, str]) -> str:
    """Resolve a project name to a project_id."""
    if not project:
        # Try building from client + product params
        client = params.get("client", "")
        product = params.get("product", "")
        if client:
            pid = _slugify(client)
            if product:
                pid = f"{pid}_{_slugify(product)}"
            return pid
        return ""

    # Try fuzzy resolution
    try:
        from corp_by_os.project_resolver import resolve_project
        resolved = resolve_project(project)
        if resolved:
            return resolved.project_id
    except Exception:
        pass

    return _slugify(project)


def _resolve_project_path(project: str, params: dict[str, str]) -> Path | None:
    """Resolve a project name to its OneDrive path."""
    # Check if project_path was provided directly
    if "project_path" in params:
        path = Path(params["project_path"])
        if path.exists():
            return path

    if not project:
        return None

    try:
        from corp_by_os.project_resolver import resolve_project
        resolved = resolve_project(project)
        if resolved and resolved.onedrive_path:
            return resolved.onedrive_path
    except Exception:
        pass

    # Try direct path
    cfg = get_config()
    for folder in cfg.projects_root.iterdir():
        if folder.is_dir() and folder.name.lower() == project.lower():
            return folder

    return None


def _serialize_issues(issues: list[dict[str, str]]) -> str:
    """Serialize issues list to YAML string for passing between steps."""
    return yaml.dump(issues, default_flow_style=False)


def _deserialize_issues(issues_str: str) -> list[dict[str, str]]:
    """Deserialize issues from YAML string."""
    try:
        data = yaml.safe_load(issues_str)
        return data if isinstance(data, list) else []
    except Exception:
        return []
