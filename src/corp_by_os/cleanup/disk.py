"""Disk space recovery — overlap detection, duplicate cleanup, artifact purge.

Separate from cleanup/scanner.py which handles file *triage*.
This module handles disk *space recovery*.

Safety: all functions default to plan-only mode. Deletions are logged
to a JSONL manifest for recovery auditing.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# OneDrive MyWork path (redundant copy of local MyWork after migration)
_DEFAULT_ONEDRIVE_MYWORK = Path(
    r"C:\Users\1028120\OneDrive - Blue Yonder\MyWork_OneDrive",
)


@dataclass
class CleanupItem:
    """A single file identified for potential deletion."""

    path: str
    filename: str
    size_bytes: int
    category: str  # overlap | duplicate | artifact
    reason: str
    keep_path: str | None = None  # path to the copy being kept


@dataclass
class CleanupPlan:
    """Aggregated cleanup plan for review before execution."""

    items: list[CleanupItem] = field(default_factory=list)
    total_bytes: int = 0
    total_files: int = 0

    @property
    def total_mb(self) -> float:
        return round(self.total_bytes / 1024 / 1024, 1)

    @property
    def total_gb(self) -> float:
        return round(self.total_bytes / 1024**3, 2)

    def add(self, item: CleanupItem) -> None:
        self.items.append(item)
        self.total_bytes += item.size_bytes
        self.total_files += 1


# === OneDrive overlap ===


def find_onedrive_overlap(
    local_root: Path,
    onedrive_root: Path | None = None,
) -> CleanupPlan:
    """Find files that exist in both local MyWork AND OneDrive MyWork.

    Matches by filename + file size. Only marks the OneDrive copy
    for deletion (local is the canonical source).

    Verifies that the local file is a real file (not a cloud placeholder)
    by checking that st_size > 0 and the file is readable.
    """
    if onedrive_root is None:
        onedrive_root = _DEFAULT_ONEDRIVE_MYWORK

    plan = CleanupPlan()

    if not onedrive_root.exists():
        logger.info("OneDrive MyWork not found: %s", onedrive_root)
        return plan

    if not local_root.exists():
        logger.warning("Local MyWork not found: %s", local_root)
        return plan

    # Index local files by (name, size)
    local_files: dict[tuple[str, int], Path] = {}
    for f in local_root.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
            if size > 0:
                local_files[(f.name, size)] = f
        except OSError:
            continue

    # Find OneDrive files with matching (name, size) in local
    for f in onedrive_root.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue

        key = (f.name, size)
        if key in local_files:
            # Verify local copy is real (not a cloud placeholder)
            local_path = local_files[key]
            if _is_real_file(local_path):
                plan.add(CleanupItem(
                    path=str(f),
                    filename=f.name,
                    size_bytes=size,
                    category="overlap",
                    reason="Exists in local MyWork (same name + size)",
                    keep_path=str(local_path),
                ))

    logger.info(
        "OneDrive overlap: %d files, %.1f MB",
        plan.total_files, plan.total_mb,
    )
    return plan


def _is_real_file(path: Path) -> bool:
    """Check that a file is a real local file, not a cloud placeholder.

    On Windows with OneDrive, cloud-only files have the
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS attribute.
    """
    try:
        stat = path.stat()
        if stat.st_size == 0:
            return False

        # Check for OneDrive cloud-only attribute (Windows-specific)
        if hasattr(stat, "st_file_attributes"):
            # FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
            recall_attr = 0x00400000
            if stat.st_file_attributes & recall_attr:  # type: ignore[attr-defined]
                return False

        return True
    except OSError:
        return False


# === Duplicate detection ===


def find_duplicates(scan_root: Path) -> CleanupPlan:
    """Find duplicate files within a directory tree.

    Groups files by (filename, size). When duplicates are found,
    keeps the file with the shortest path (most canonical location)
    and marks the rest for deletion.
    """
    plan = CleanupPlan()

    # Group files by (name, size)
    groups: dict[tuple[str, int], list[Path]] = {}
    for f in scan_root.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue
        key = (f.name, size)
        groups.setdefault(key, []).append(f)

    for (filename, size), paths in groups.items():
        if len(paths) < 2:
            continue

        # Keep the copy with the shortest path (most canonical)
        paths.sort(key=lambda p: len(str(p)))
        keep = paths[0]

        for dup in paths[1:]:
            plan.add(CleanupItem(
                path=str(dup),
                filename=filename,
                size_bytes=size,
                category="duplicate",
                reason=f"Duplicate of {keep.name} (same name + size)",
                keep_path=str(keep),
            ))

    logger.info(
        "Duplicates: %d files, %.1f MB",
        plan.total_files, plan.total_mb,
    )
    return plan


# === CKE artifact cleanup ===


def find_extraction_artifacts(mywork_root: Path) -> CleanupPlan:
    """Find CKE extraction run artifacts in .corp/run/.

    CKE copies source files during extraction. The originals still
    exist in MyWork, so these copies are safe to remove.
    """
    plan = CleanupPlan()

    run_dir = mywork_root / "90_System" / ".corp" / "run"
    if not run_dir.exists():
        logger.info("No .corp/run/ directory found")
        return plan

    for f in run_dir.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue

        plan.add(CleanupItem(
            path=str(f),
            filename=f.name,
            size_bytes=size,
            category="artifact",
            reason="CKE extraction run artifact (originals in MyWork)",
        ))

    logger.info(
        "Extraction artifacts: %d files, %.1f MB",
        plan.total_files, plan.total_mb,
    )
    return plan


# === Staging cleanup ===


def find_staging_artifacts(app_data_path: Path) -> CleanupPlan:
    """Find stale staging files in corp-by-os app data."""
    plan = CleanupPlan()

    staging_dir = app_data_path / "staging"
    if not staging_dir.exists():
        return plan

    for f in staging_dir.rglob("*"):
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
        except OSError:
            continue

        plan.add(CleanupItem(
            path=str(f),
            filename=f.name,
            size_bytes=size,
            category="artifact",
            reason="Stale staging artifact from ingest/extraction",
        ))

    logger.info(
        "Staging artifacts: %d files, %.1f MB",
        plan.total_files, plan.total_mb,
    )
    return plan


# === Execution ===


def execute_plan(
    plan: CleanupPlan,
    log_path: Path,
    dry_run: bool = True,
) -> tuple[int, int]:
    """Execute a cleanup plan — delete files and log to manifest.

    Returns (deleted_count, failed_count).
    """
    deleted = 0
    failed = 0

    log_path.parent.mkdir(parents=True, exist_ok=True)

    for item in plan.items:
        target = Path(item.path)

        if dry_run:
            logger.info("[DRY RUN] Would delete: %s (%.1f MB)",
                        item.filename, item.size_bytes / 1024 / 1024)
            continue

        if not target.exists():
            logger.warning("File not found, skipping: %s", item.path)
            failed += 1
            continue

        try:
            target.unlink()
            deleted += 1
            _log_deletion(log_path, item)
            logger.info("Deleted: %s (%.1f MB)",
                        item.filename, item.size_bytes / 1024 / 1024)
        except OSError as exc:
            logger.error("Failed to delete %s: %s", item.path, exc)
            failed += 1

    # Clean up empty directories after deletion
    if not dry_run:
        _cleanup_empty_dirs(plan)

    return deleted, failed


def _log_deletion(log_path: Path, item: CleanupItem) -> None:
    """Append a deletion record to the JSONL cleanup log."""
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "action": "deleted",
        "path": item.path,
        "filename": item.filename,
        "size_bytes": item.size_bytes,
        "category": item.category,
        "reason": item.reason,
        "keep_path": item.keep_path,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _cleanup_empty_dirs(plan: CleanupPlan) -> None:
    """Remove directories that became empty after deletion."""
    dirs_to_check: set[Path] = set()
    for item in plan.items:
        dirs_to_check.add(Path(item.path).parent)

    for d in sorted(dirs_to_check, key=lambda p: len(str(p)), reverse=True):
        try:
            if d.exists() and d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                logger.debug("Removed empty dir: %s", d)
        except OSError:
            pass


# === Guidance text ===


APPDATA_GUIDANCE = """\
AppData cleanup (manual — not scriptable):

  Microsoft Teams cache:  Clear via Teams Settings → Storage
  Google Chrome cache:    chrome://settings → Privacy → Clear browsing data
  Windows temp files:     Settings → System → Storage → Temporary files
  Temp folder:            %TEMP% — safe to clear
  corp-by-os staging:     %LOCALAPPDATA%\\corp-by-os\\staging — safe after verified
"""

PAGEFILE_GUIDANCE = """\
Pagefile reduction (~30 GB savings — requires admin):

  1. Win+R → sysdm.cpl → Advanced tab → Performance Settings
  2. Advanced tab → Virtual Memory → Change
  3. Uncheck "Automatically manage paging file size"
  4. Select C: drive → Custom size
  5. Initial size: 8192 MB  |  Maximum size: 16384 MB
  6. Click Set → OK → Restart

  Current: ~45 GB allocated, ~6 GB used
  Recommended: 8-16 GB (saves ~30 GB)
"""
