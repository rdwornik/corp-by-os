"""Ingest router — detect, match, route, extract, record.

Processes files from 00_Inbox (or explicit paths) through the
content registry, stages or routes them to their destination,
optionally extracts via CKE, and records everything in ops.db.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from corp_by_os.ops.database import OpsDB
from corp_by_os.ops.registry import ContentRegistry

logger = logging.getLogger(__name__)

# Infrastructure files/folders to skip during inbox scan
_SKIP_NAMES = {
    ".corp",
    "_knowledge",
    "__pycache__",
    ".git",
    ".venv",
    "desktop.ini",
    "Thumbs.db",
    ".DS_Store",
    "_triage_log.jsonl",
    "_triage_schema.yaml",
    "folder_manifest.yaml",
}

# Directories in Inbox that are infrastructure, not packages
_SKIP_DIRS = {"_Unmatched", "_Staging", ".corp", "_knowledge", "__pycache__"}

_SKIP_EXTENSIONS = {".tmp", ".crdownload", ".partial"}


@dataclass
class InboxItem:
    """An item found in Inbox — either a file or a folder."""

    path: Path
    is_folder: bool
    file_count: int = 0
    total_size_bytes: int = 0
    depth: int = 0


@dataclass
class PackageIngestResult:
    """Result of ingesting a folder package."""

    folder_name: str
    source_path: str
    destination_path: str
    action: str  # routed | staged | quarantined | error
    match_method: str
    match_series: str | None
    confidence: float
    file_count: int
    total_size_mb: float
    extracted: bool
    extraction_cost: float
    error: str | None


@dataclass
class IngestResult:
    """Outcome of ingesting a single file."""

    filename: str
    source_path: str
    destination_path: str | None = None
    action: str = "error"  # routed | staged | quarantined | skipped | error
    match_method: str = "none"
    match_series: str | None = None
    confidence: float = 0.0
    extracted: bool = False
    extraction_cost: float = 0.0
    vault_note_path: str | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_inbox(mywork_root: Path) -> list[InboxItem]:
    """Find all files AND folders in 00_Inbox that should be ingested.

    Returns InboxItem objects. Folders are detected at depth 1 only
    (direct children of 00_Inbox). Files inside those folders are NOT
    listed separately — they're part of the folder package.

    Loose files (directly in Inbox root, not in a subfolder) are
    returned as individual InboxItems with is_folder=False.
    """
    inbox = mywork_root / "00_Inbox"
    if not inbox.exists():
        logger.warning("Inbox not found: %s", inbox)
        return []

    items: list[InboxItem] = []

    for entry in sorted(inbox.iterdir()):
        if entry.name in _SKIP_NAMES or entry.name.startswith("."):
            continue
        if entry.name in _SKIP_DIRS:
            continue

        if entry.is_dir():
            file_list = [f for f in entry.rglob("*") if f.is_file()]
            max_depth = max(
                (len(f.relative_to(entry).parts) for f in file_list),
                default=0,
            )
            total_size = sum(f.stat().st_size for f in file_list)
            items.append(
                InboxItem(
                    path=entry,
                    is_folder=True,
                    file_count=len(file_list),
                    total_size_bytes=total_size,
                    depth=max_depth,
                )
            )
        elif entry.is_file():
            if entry.suffix.lower() in _SKIP_EXTENSIONS:
                continue
            items.append(InboxItem(path=entry, is_folder=False))

    folder_count = sum(1 for i in items if i.is_folder)
    file_count = len(items) - folder_count
    logger.info(
        "Inbox scan: %d file(s) and %d folder(s) found",
        file_count,
        folder_count,
    )
    return items


def ingest_file(
    file_path: Path,
    mywork_root: Path,
    ops: OpsDB,
    registry: ContentRegistry,
    *,
    extract: bool = True,
    dry_run: bool = False,
) -> IngestResult:
    """Run the full ingest pipeline on a single file.

    Steps: detect → match → route → extract → record → report.
    """
    file_path = file_path.resolve()
    filename = file_path.name
    extension = file_path.suffix.lower()
    parent = file_path.parent

    # Build folder context (relative to mywork_root)
    try:
        folder_context = str(parent.relative_to(mywork_root.resolve())).replace("\\", "/")
    except ValueError:
        folder_context = None

    result = IngestResult(
        filename=filename,
        source_path=str(file_path).replace("\\", "/"),
    )

    # Step 1: Detect — gather file metadata
    if not file_path.exists():
        result.error = "File not found"
        return result

    size_bytes = file_path.stat().st_size
    mtime = file_path.stat().st_mtime
    mtime_str = datetime.fromtimestamp(mtime).isoformat(timespec="seconds")

    # Step 2: Match — query ContentRegistry
    match = registry.match_file(filename, extension, folder_context=folder_context)
    result.match_method = match.method
    result.match_series = match.series_id
    result.confidence = match.confidence
    result.metadata = match.metadata

    # Compute content hash
    content_hash = compute_file_hash(file_path)

    # Step 3: Route — determine destination and action
    fallback = registry.get_fallback_config()
    confidence_threshold = fallback.get("confidence_threshold", 0.75)

    if not match.matched:
        # No match — quarantine to _Unmatched
        dest_rel = match.destination or "00_Inbox/_Unmatched"
        result.action = "quarantined"
    elif match.confidence >= confidence_threshold:
        # High confidence — route directly
        dest_rel = match.destination
        result.action = "routed"
    else:
        # Low confidence — stage for review
        dest_rel = f"{match.destination}/_Staging" if match.destination else "00_Inbox/_Staging"
        result.action = "staged"

    result.destination_path = dest_rel

    # Derive folder levels from destination
    dest_parts = dest_rel.replace("\\", "/").split("/") if dest_rel else []
    folder_l1 = dest_parts[0] if dest_parts else "00_Inbox"
    folder_l2 = dest_parts[1] if len(dest_parts) > 1 else None

    # Step 4: Record in ops.db (before moving, so we have the record even if move fails)
    source_rel = (
        str(file_path.relative_to(mywork_root.resolve())).replace("\\", "/")
        if folder_context
        else filename
    )

    if not dry_run:
        asset_id = ops.upsert_asset(
            path=source_rel,
            filename=filename,
            extension=extension,
            size_bytes=size_bytes,
            mtime=mtime_str,
            folder_l1=folder_l1,
            folder_l2=folder_l2,
        )
    else:
        asset_id = None

    # Step 5: Move file to destination
    if not dry_run and dest_rel:
        dest_dir = mywork_root / dest_rel
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / filename

        # Handle name collision
        if dest_file.exists():
            stem = file_path.stem
            suffix = file_path.suffix
            counter = 1
            while dest_file.exists():
                dest_file = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        try:
            shutil.move(str(file_path), str(dest_file))
            logger.info(
                "Moved: %s -> %s (%s, conf=%.2f)",
                filename,
                dest_rel,
                result.action,
                result.confidence,
            )

            # Update asset path to new location, then record status
            new_rel = str(dest_file.relative_to(mywork_root.resolve())).replace("\\", "/")
            ops.update_asset_path(source_rel, new_rel)
            ops.update_asset_status(
                new_rel,
                result.action,
                routed_to=new_rel,
                routed_method=match.method,
                routed_confidence=match.confidence,
                reasoning=f"Registry match: {match.method}"
                + (f" (series={match.series_id})" if match.series_id else "")
                + (f" (rule={match.rule_name})" if match.rule_name else ""),
            )
        except OSError as exc:
            result.action = "error"
            result.error = f"Move failed: {exc}"
            logger.error("Move failed for %s: %s", filename, exc)
            return result

    # Step 6: Extract via CKE (optional)
    if extract and not dry_run and result.action in ("routed", "staged"):
        try:
            vault_note, cost = _run_extraction(
                dest_file if not dry_run else file_path,
                mywork_root,
                ops,
                asset_id,
                content_hash,
                mtime_str,
            )
            result.extracted = vault_note is not None
            result.extraction_cost = cost
            result.vault_note_path = vault_note
        except Exception as exc:
            logger.error("Extraction failed for %s: %s", filename, exc)
            result.error = f"Extraction failed: {exc}"
            # Don't change action — the file is already routed/staged

    return result


def ingest_all(
    mywork_root: Path,
    ops: OpsDB,
    registry: ContentRegistry,
    *,
    extract: bool = True,
    dry_run: bool = False,
) -> tuple[list[IngestResult], list[PackageIngestResult]]:
    """Scan inbox and ingest all files and folders.

    Returns (file_results, package_results).
    """
    items = scan_inbox(mywork_root)
    if not items:
        logger.info("Inbox is empty — nothing to ingest.")
        return [], []

    folders = [i for i in items if i.is_folder]
    files = [i for i in items if not i.is_folder]

    logger.info(
        "Found %d file(s) and %d folder(s) in Inbox.",
        len(files),
        len(folders),
    )

    file_results: list[IngestResult] = []
    package_results: list[PackageIngestResult] = []

    # Process folders first (they're larger units)
    for item in folders:
        result = ingest_folder(
            item.path,
            mywork_root,
            ops,
            registry,
            extract=extract,
            dry_run=dry_run,
        )
        package_results.append(result)

    # Process loose files
    for item in files:
        result = ingest_file(
            item.path,
            mywork_root,
            ops,
            registry,
            extract=extract,
            dry_run=dry_run,
        )
        file_results.append(result)

    return file_results, package_results


def ingest_folder(
    folder_path: Path,
    mywork_root: Path,
    ops: OpsDB,
    registry: ContentRegistry,
    *,
    extract: bool = True,
    dry_run: bool = False,
) -> PackageIngestResult:
    """Ingest a folder as a coherent package.

    Steps: pre-flight → package record → match → route → move →
    register files → extract → log events.
    """
    folder_path = folder_path.resolve()
    try:
        rel_str = str(folder_path.relative_to(mywork_root.resolve())).replace("\\", "/")
    except ValueError:
        rel_str = folder_path.name

    # --- Step 1: Pre-flight ---
    file_list = [f for f in folder_path.rglob("*") if f.is_file()]
    file_count = len(file_list)
    total_size = sum(f.stat().st_size for f in file_list)
    total_size_mb = round(total_size / 1024 / 1024, 2)
    max_depth = max(
        (len(f.relative_to(folder_path).parts) for f in file_list),
        default=0,
    )

    if max_depth > 3 or total_size > 500 * 1024 * 1024:
        logger.warning(
            "Large folder: %s (depth=%d, size=%.1fMB, files=%d). "
            "Consider reviewing before ingesting.",
            folder_path.name,
            max_depth,
            total_size_mb,
            file_count,
        )

    def _error_result(error: str) -> PackageIngestResult:
        return PackageIngestResult(
            folder_name=folder_path.name,
            source_path=rel_str,
            destination_path="",
            action="error",
            match_method="none",
            match_series=None,
            confidence=0.0,
            file_count=file_count,
            total_size_mb=total_size_mb,
            extracted=False,
            extraction_cost=0.0,
            error=error,
        )

    if file_count == 0:
        return _error_result("Empty folder — no files to ingest")

    # --- Step 2: Create Package record ---
    if not dry_run:
        package_id = ops.create_package(
            folder_name=folder_path.name,
            source_path=rel_str,
            file_count=file_count,
            total_size=total_size,
        )
    else:
        package_id = None

    # --- Step 3: Match against registry ---
    match = registry.match_folder(folder_path.name)

    # --- Step 4: Determine routing ---
    fallback = registry.get_fallback_config()
    confidence_threshold = fallback.get("confidence_threshold", 0.75)

    if not match.matched:
        action = "quarantined"
        dest_folder = fallback.get("unknown_destination", "00_Inbox/_Unmatched")
    elif match.confidence >= confidence_threshold:
        action = "routed"
        dest_folder = match.destination
    else:
        action = "staged"
        dest_folder = f"{match.destination}/_Staging" if match.destination else "00_Inbox/_Staging"

    # --- Step 5: Normalize folder name ---
    normalized_name = folder_path.name.replace(" ", "_")
    while "__" in normalized_name:
        normalized_name = normalized_name.replace("__", "_")

    dest_full = f"{dest_folder}/{normalized_name}"

    if dry_run:
        return PackageIngestResult(
            folder_name=folder_path.name,
            source_path=rel_str,
            destination_path=dest_full,
            action=action,
            match_method=match.method,
            match_series=match.series_id,
            confidence=match.confidence,
            file_count=file_count,
            total_size_mb=total_size_mb,
            extracted=False,
            extraction_cost=0.0,
            error=None,
        )

    # --- Step 6: Move entire folder ---
    destination = mywork_root / dest_full.replace("/", "\\")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            normalized_name = f"{normalized_name}_{ts}"
            dest_full = f"{dest_folder}/{normalized_name}"
            destination = mywork_root / dest_full.replace("/", "\\")

        shutil.move(str(folder_path), str(destination))
        logger.info(
            "Moved folder: %s → %s (%d files, %.1fMB)",
            folder_path.name,
            dest_full,
            file_count,
            total_size_mb,
        )
    except OSError as exc:
        logger.error("Failed to move folder %s: %s", folder_path.name, exc)
        return _error_result(f"Move failed: {exc}")

    # --- Step 7: Register all files as assets ---
    dest_rel = str(destination.relative_to(mywork_root.resolve())).replace("\\", "/")

    for f in destination.rglob("*"):
        if not f.is_file():
            continue
        f_rel = str(f.relative_to(mywork_root.resolve())).replace("\\", "/")
        f_stat = f.stat()
        f_parts = f.relative_to(mywork_root.resolve()).parts
        ops.upsert_asset(
            path=f_rel,
            filename=f.name,
            extension=f.suffix.lower(),
            size_bytes=f_stat.st_size,
            mtime=datetime.fromtimestamp(f_stat.st_mtime).isoformat(
                timespec="seconds",
            ),
            folder_l1=f_parts[0] if f_parts else "",
            folder_l2=f_parts[1] if len(f_parts) > 1 else None,
        )
        # Link asset to package
        ops.conn.execute(
            "UPDATE assets SET package_id = ?, status = ? WHERE path = ?",
            (package_id, action, f_rel),
        )
    ops.conn.commit()

    # Log package event
    ops.log_event(
        action=action,
        package_id=package_id,
        source_path=rel_str,
        destination_path=dest_rel,
        method=match.method,
        confidence=match.confidence,
        reasoning=(
            f"Folder package: series={match.series_id}, rule={match.rule_name}, {file_count} files"
        ),
    )

    # --- Step 8: Extract with shared context ---
    extracted = False
    extraction_cost = 0.0

    if extract and action in ("routed", "staged"):
        try:
            extraction_cost = _run_package_extraction(
                destination,
                mywork_root,
                ops,
                package_id,
                shared_context=f"All files in this folder relate to: {folder_path.name}",
            )
            extracted = True
            ops.update_package_status(
                package_id,
                "extracted",
                destination_path=dest_rel,
            )
        except Exception as exc:
            logger.warning(
                "Package extraction failed for %s: %s",
                folder_path.name,
                exc,
            )

    return PackageIngestResult(
        folder_name=folder_path.name,
        source_path=rel_str,
        destination_path=dest_rel,
        action=action,
        match_method=match.method,
        match_series=match.series_id,
        confidence=match.confidence,
        file_count=file_count,
        total_size_mb=total_size_mb,
        extracted=extracted,
        extraction_cost=extraction_cost,
        error=None,
    )


def _run_package_extraction(
    folder_path: Path,
    mywork_root: Path,
    ops: OpsDB,
    package_id: int | None,
    shared_context: str = "",
) -> float:
    """Run batch extraction on all files in a folder package.

    Reuses the existing CKE extraction pipeline — builds a multi-file
    manifest and calls extract_sync. The shared_context is passed so
    all extractions share folder-level topic context.

    Returns total API cost.
    """
    from corp_by_os.extraction.non_project.manifest_emitter import (
        _make_entry_id,
        _resolve_doc_type,
    )
    from corp_by_os.overnight.cke_client import extract_sync, is_available

    ok, err = is_available()
    if not ok:
        logger.warning("CKE not available, skipping package extraction: %s", err)
        return 0.0

    from corp_by_os.config import get_config

    cfg = get_config()

    # Build a multi-file manifest for the folder
    pkg_id = _make_entry_id(folder_path.name)
    staging_dir = cfg.app_data_path / "staging" / "ingest" / pkg_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    file_entries = []
    for f in sorted(folder_path.rglob("*")):
        if not f.is_file():
            continue
        entry_id = _make_entry_id(
            str(f.relative_to(folder_path)).replace("\\", "/"),
        )
        file_entries.append(
            {
                "id": entry_id,
                "path": str(f),
                "doc_type": _resolve_doc_type(f.suffix),
                "name": f.stem,
                "content_origin": "mywork_ingest",
                "source_category": "ingest_package",
                "source_locator": str(f.relative_to(mywork_root.resolve())).replace("\\", "/"),
                "shared_context": shared_context,
            }
        )

    if not file_entries:
        return 0.0

    manifest = {
        "schema_version": 1,
        "project": f"ingest_pkg_{pkg_id}",
        "output_dir": str(staging_dir),
        "files": file_entries,
    }

    manifest_path = staging_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    result = extract_sync(manifest_path)
    cost = result.get("cost", 0.0)
    done = result.get("done", 0)

    if done == 0:
        return cost

    # Move to vault
    from corp_by_os.extraction.vault_writer import move_to_vault

    vault_target = "70_Extracts/Ingest"
    move_to_vault(staging_dir, cfg.vault_path, vault_target)

    return cost


def _run_extraction(
    file_path: Path,
    mywork_root: Path,
    ops: OpsDB,
    asset_id: int | None,
    content_hash: str,
    mtime_str: str,
) -> tuple[str | None, float]:
    """Invoke CKE extraction on a single file.

    Reuses the existing extraction pipeline:
    - Builds a minimal CKE manifest for the single file
    - Calls extract_sync (single file doesn't justify batch API)
    - Moves output to vault via vault_writer

    Returns (vault_note_path | None, cost).
    """
    from corp_by_os.extraction.non_project.manifest_emitter import (
        _make_entry_id,
        _resolve_doc_type,
    )
    from corp_by_os.overnight.cke_client import extract_sync, is_available

    ok, err = is_available()
    if not ok:
        logger.warning("CKE not available, skipping extraction: %s", err)
        return None, 0.0

    from corp_by_os.config import get_config

    cfg = get_config()

    # Build a minimal manifest for this single file
    entry_id = _make_entry_id(file_path.name)
    staging_dir = cfg.app_data_path / "staging" / "ingest" / entry_id
    staging_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": 1,
        "project": "ingest",
        "output_dir": str(staging_dir),
        "files": [
            {
                "id": entry_id,
                "path": str(file_path),
                "doc_type": _resolve_doc_type(file_path.suffix),
                "name": file_path.stem,
                "content_origin": "mywork_ingest",
                "source_category": "ingest",
                "source_locator": str(file_path.relative_to(mywork_root.resolve())).replace(
                    "\\", "/"
                ),
            }
        ],
    }

    manifest_path = staging_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # Run sync extraction (single file)
    result = extract_sync(manifest_path)
    cost = result.get("cost", 0.0)
    done = result.get("done", 0)

    if done == 0:
        return None, cost

    # Move to vault
    from corp_by_os.extraction.vault_writer import move_to_vault

    # Determine vault target from the file's destination
    vault_target = "70_Extracts/Ingest"  # default
    moved = move_to_vault(staging_dir, cfg.vault_path, vault_target)

    vault_note_path = f"{vault_target}/{entry_id}" if moved > 0 else None

    # Update ops.db with extraction info
    if asset_id is not None and vault_note_path:
        ops.update_asset_status(
            str(file_path.relative_to(mywork_root.resolve())).replace("\\", "/"),
            "extracted",
            extracted_note_path=vault_note_path,
            source_hash_at_extraction=content_hash,
            cost=cost,
            reasoning="CKE sync extraction via ingest",
        )

    return vault_note_path, cost


def get_staged_files(mywork_root: Path) -> list[dict]:
    """Find all files in _Staging directories across MyWork.

    Returns list of dicts with path, filename, staging_parent info.
    """
    staged: list[dict] = []
    for staging_dir in sorted(mywork_root.rglob("_Staging")):
        if not staging_dir.is_dir():
            continue
        parent_dest = str(staging_dir.parent.relative_to(mywork_root.resolve())).replace("\\", "/")

        for f in sorted(staging_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name in _SKIP_NAMES:
                continue
            staged.append(
                {
                    "path": str(f),
                    "filename": f.name,
                    "staging_dir": str(staging_dir),
                    "parent_destination": parent_dest,
                }
            )

    return staged


def finalize_file(
    staged_path: Path,
    mywork_root: Path,
    ops: OpsDB,
) -> bool:
    """Move a staged file from _Staging/ to its parent destination.

    Returns True if successful.
    """
    if not staged_path.exists():
        logger.warning("Staged file not found: %s", staged_path)
        return False

    # _Staging is always one level below the destination
    staging_dir = staged_path.parent
    if staging_dir.name != "_Staging":
        logger.warning("File is not in a _Staging directory: %s", staged_path)
        return False

    dest_dir = staging_dir.parent
    dest_file = dest_dir / staged_path.name

    # Handle collision
    if dest_file.exists():
        stem = staged_path.stem
        suffix = staged_path.suffix
        counter = 1
        while dest_file.exists():
            dest_file = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        shutil.move(str(staged_path), str(dest_file))
    except OSError as exc:
        logger.error("Finalize move failed: %s", exc)
        return False

    # Update ops.db
    try:
        source_rel = str(staged_path.relative_to(mywork_root.resolve())).replace("\\", "/")
        dest_rel = str(dest_file.relative_to(mywork_root.resolve())).replace("\\", "/")

        ops.update_asset_status(
            source_rel,
            "routed",
            routed_to=dest_rel,
            reasoning="Finalized from staging by user review",
        )
    except (ValueError, Exception) as exc:
        logger.warning("Could not update ops.db for finalize: %s", exc)

    logger.info("Finalized: %s -> %s", staged_path.name, dest_dir)
    return True
