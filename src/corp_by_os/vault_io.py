"""Vault IO — the single writer to the Obsidian vault.

All agents write to vault THROUGH this module (via corp-by-os workflows).
Direct agent writes allowed for now but should migrate here.

Key responsibilities:
- Resolve project paths (OneDrive <-> Vault)
- Read/write notes with frontmatter handling
- Idempotent writes (check before creating)
- File-lock retry with exponential backoff (OneDrive sync conflicts)
- Path normalization for Windows
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

import yaml

from corp_by_os.config import get_config
from corp_by_os.models import (
    Mutability,
    ProjectInfo,
    ProjectSummary,
    ValidationIssue,
    ValidationReport,
    VaultPath,
    VaultZone,
    ZONE_MUTABILITY,
)

logger = logging.getLogger(__name__)

FRONTMATTER_SEP = "---"


# --- Path resolution ---


def resolve_vault_path(
    zone: VaultZone | str,
    project_id: str | None = None,
    filename: str | None = None,
) -> VaultPath:
    """Build an absolute vault path from zone/project/filename components."""
    cfg = get_config()
    if isinstance(zone, str):
        zone = VaultZone(zone)

    parts: list[str] = [zone.value]
    if project_id:
        parts.append(project_id)
    if filename:
        parts.append(filename)

    absolute = cfg.vault_path
    for p in parts:
        absolute = absolute / p

    return VaultPath(
        zone=zone,
        project_id=project_id,
        filename=filename,
        absolute=absolute,
    )


# --- Frontmatter parsing ---


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Split a note into (frontmatter_dict, body_str).

    Returns empty dict if no frontmatter found.
    """
    if not content.startswith(FRONTMATTER_SEP):
        return {}, content

    # Find the closing ---
    end_idx = content.index("\n" + FRONTMATTER_SEP, len(FRONTMATTER_SEP))
    fm_text = content[len(FRONTMATTER_SEP) + 1 : end_idx]
    body = content[end_idx + len(FRONTMATTER_SEP) + 2 :]  # skip \n---\n

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        logger.warning("Failed to parse frontmatter, returning raw")
        fm = {}

    return fm, body


def _render_note(frontmatter: dict[str, Any], body: str) -> str:
    """Render frontmatter + body into a full note string."""
    if frontmatter:
        fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True).strip()
        return f"{FRONTMATTER_SEP}\n{fm_str}\n{FRONTMATTER_SEP}\n{body}"
    return body


# --- File IO with retry ---


def _write_with_retry(path: Path, content: str, max_retries: int = 5) -> bool:
    """Write with exponential backoff for OneDrive locks."""
    for attempt in range(max_retries):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True
        except (PermissionError, OSError) as e:
            if attempt < max_retries - 1:
                wait = 0.1 * (2**attempt)  # 100ms, 200ms, 400ms, 800ms, 1600ms
                logger.warning("Write blocked (%s), retry in %.1fs: %s", e, wait, path)
                time.sleep(wait)
            else:
                raise
    return False  # unreachable, but satisfies type checker


# --- Core operations ---


def read_note(path: Path) -> tuple[dict[str, Any], str]:
    """Read a note, returning (frontmatter_dict, body_str)."""
    content = path.read_text(encoding="utf-8")
    return _parse_frontmatter(content)


def write_note(
    path: Path,
    frontmatter: dict[str, Any],
    body: str,
    mode: str = "upsert",
) -> bool:
    """Write a note to the vault.

    Args:
        path: Absolute path to the note file.
        frontmatter: YAML frontmatter dict.
        body: Note body text.
        mode: "create" (fail if exists), "update" (fail if missing),
              "upsert" (create or update).

    Returns:
        True if written successfully.

    Raises:
        FileExistsError: If mode="create" and file exists.
        FileNotFoundError: If mode="update" and file missing.
    """
    if mode == "create" and path.exists():
        raise FileExistsError(f"Note already exists: {path}")
    if mode == "update" and not path.exists():
        raise FileNotFoundError(f"Note not found: {path}")

    content = _render_note(frontmatter, body)
    return _write_with_retry(path, content)


def _read_project_info_from_path(info_file: Path, project_id: str) -> ProjectInfo | None:
    """Read ProjectInfo from a specific project-info.yaml file."""
    try:
        with open(info_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return None

    if not data or not isinstance(data, dict):
        return None

    return ProjectInfo(
        project_id=data.get("project_id", project_id),
        client=data.get("client", ""),
        status=data.get("status", "unknown"),
        products=data.get("products", []),
        topics=data.get("topics", []),
        domains=data.get("domains", []),
        files_processed=data.get("files_processed", 0),
        facts_count=data.get("facts_count", 0),
        last_extracted=data.get("last_extracted"),
        people=data.get("people", []),
        stage=data.get("stage"),
        opportunity_id=data.get("opportunity_id"),
        region=data.get("region"),
        industry=data.get("industry"),
    )


def _find_vault_project_dir(project_id: str) -> Path | None:
    """Find vault project dir with case-insensitive matching."""
    cfg = get_config()
    projects_dir = cfg.vault_path / VaultZone.PROJECTS.value
    if not projects_dir.exists():
        return None

    # Try exact match first
    exact = projects_dir / project_id
    if exact.exists():
        return exact

    # Case-insensitive scan
    pid_lower = project_id.lower()
    for folder in projects_dir.iterdir():
        if folder.is_dir() and folder.name.lower() == pid_lower:
            return folder

    return None


def read_project_info(project_id: str) -> ProjectInfo | None:
    """Read project-info.yaml for a project from the vault."""
    project_dir = _find_vault_project_dir(project_id)
    if not project_dir:
        return None

    info_file = project_dir / "project-info.yaml"
    if not info_file.exists():
        return None

    return _read_project_info_from_path(info_file, project_id)


def list_projects(status_filter: str | None = None) -> list[ProjectSummary]:
    """List all projects by merging OneDrive folders + vault folders.

    Returns a deduplicated list ordered alphabetically by project_id.
    """
    cfg = get_config()
    projects: dict[str, ProjectSummary] = {}

    # Scan OneDrive project folders
    if cfg.projects_root.exists():
        for folder in sorted(cfg.projects_root.iterdir()):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                pid = folder.name.lower()
                projects[pid] = ProjectSummary(
                    project_id=pid,
                    client=folder.name.split("_")[0],
                    status="unknown",
                    has_vault=False,
                    has_onedrive=True,
                    onedrive_path=folder,
                )

    # Scan vault project folders and merge
    vault_projects = cfg.vault_path / VaultZone.PROJECTS.value
    if vault_projects.exists():
        for folder in sorted(vault_projects.iterdir()):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                pid = folder.name.lower()
                if pid in projects:
                    projects[pid].has_vault = True
                    projects[pid].vault_path = folder
                else:
                    projects[pid] = ProjectSummary(
                        project_id=pid,
                        client=folder.name.split("_")[0],
                        status="unknown",
                        has_vault=True,
                        has_onedrive=False,
                        vault_path=folder,
                    )

    # Enrich with project-info.yaml data where available
    for pid, summary in projects.items():
        # Try vault path first (preserves actual folder casing)
        info = None
        if summary.vault_path:
            info_file = summary.vault_path / "project-info.yaml"
            if info_file.exists():
                info = _read_project_info_from_path(info_file, pid)
        if info is None:
            info = read_project_info(pid)
        if info:
            summary.client = info.client
            summary.status = info.status
            summary.facts_count = info.facts_count

    # Apply filter
    result = sorted(projects.values(), key=lambda p: p.project_id)
    if status_filter:
        result = [p for p in result if p.status == status_filter]

    return result


def copy_to_vault(
    source: Path,
    zone: VaultZone | str,
    project_id: str,
) -> list[Path]:
    """Copy files from source directory to vault zone/project.

    Respects mutability rules:
    - IMMUTABLE: skip files that already exist
    - REGENERABLE: overwrite
    - PROTECTED: skip existing files
    """
    if isinstance(zone, str):
        zone = VaultZone(zone)

    mutability = ZONE_MUTABILITY.get(zone, Mutability.REGENERABLE)
    vp = resolve_vault_path(zone, project_id)
    dest_dir = vp.absolute
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []

    if not source.is_dir():
        logger.error("Source is not a directory: %s", source)
        return copied

    for src_file in source.rglob("*"):
        if src_file.is_dir():
            continue

        rel = src_file.relative_to(source)
        dest = dest_dir / rel

        # Respect mutability
        if dest.exists() and mutability in (Mutability.IMMUTABLE, Mutability.PROTECTED):
            logger.debug("Skipping existing file (%s zone): %s", mutability.value, dest)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest)
        copied.append(dest)
        logger.debug("Copied %s -> %s", src_file, dest)

    logger.info("Copied %d files to %s/%s", len(copied), zone.value, project_id)
    return copied


def validate_vault(project_id: str | None = None) -> ValidationReport:
    """Validate vault structure and frontmatter.

    Uses corp-os-meta's validate_frontmatter for .md files.
    Checks project-info.yaml exists and has required fields.
    """
    cfg = get_config()
    report = ValidationReport(project_id=project_id)

    # Determine which project folders to check
    projects_dir = cfg.vault_path / VaultZone.PROJECTS.value
    if project_id:
        found = _find_vault_project_dir(project_id)
        folders = [found] if found else [projects_dir / project_id]
    elif projects_dir.exists():
        folders = [f for f in sorted(projects_dir.iterdir()) if f.is_dir()]
    else:
        report.issues.append(ValidationIssue(
            path=projects_dir, level="error", message="Projects directory not found",
        ))
        return report

    for folder in folders:
        if not folder.exists():
            report.issues.append(ValidationIssue(
                path=folder, level="error", message="Project folder not found",
            ))
            continue

        # Check project-info.yaml
        info_file = folder / "project-info.yaml"
        if not info_file.exists():
            report.issues.append(ValidationIssue(
                path=info_file, level="warning", message="Missing project-info.yaml",
            ))
        else:
            _validate_project_info(info_file, report)

        # Validate .md frontmatter via corp-os-meta
        for md_file in folder.rglob("*.md"):
            report.notes_checked += 1
            _validate_note_frontmatter(md_file, report)

    # Also check sources if project_id specified
    if project_id:
        sources_dir = cfg.vault_path / VaultZone.SOURCES.value / project_id
        if sources_dir.exists():
            for md_file in sources_dir.rglob("*.md"):
                report.notes_checked += 1
                _validate_note_frontmatter(md_file, report)

    return report


def _validate_project_info(path: Path, report: ValidationReport) -> None:
    """Check project-info.yaml has required fields."""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        report.issues.append(ValidationIssue(
            path=path, level="error", message=f"Failed to parse YAML: {e}",
        ))
        return

    if not isinstance(data, dict):
        report.issues.append(ValidationIssue(
            path=path, level="error", message="project-info.yaml is not a valid mapping",
        ))
        return

    required = ["project_id", "client", "status"]
    for field_name in required:
        if field_name not in data:
            report.issues.append(ValidationIssue(
                path=path, level="error",
                message=f"Missing required field: {field_name}",
            ))


def _validate_note_frontmatter(path: Path, report: ValidationReport) -> None:
    """Validate a single .md note's frontmatter using corp-os-meta."""
    try:
        from corp_os_meta import ValidationResult as VR
        from corp_os_meta import validate_frontmatter

        content = path.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(content)
        if not fm:
            report.issues.append(ValidationIssue(
                path=path, level="warning", message="No frontmatter found",
            ))
            return

        status, _model, issues = validate_frontmatter(fm)
        if status == VR.VALID:
            report.notes_valid += 1
        elif status == VR.WARNINGS:
            report.notes_valid += 1
            for issue in issues:
                report.issues.append(ValidationIssue(
                    path=path, level="warning", message=str(issue),
                ))
        else:  # QUARANTINE
            for issue in issues:
                report.issues.append(ValidationIssue(
                    path=path, level="error", message=str(issue),
                ))
    except ImportError:
        # corp-os-meta not available — skip frontmatter validation
        report.notes_valid += 1
    except Exception as e:
        report.issues.append(ValidationIssue(
            path=path, level="warning", message=f"Validation error: {e}",
        ))
