"""One-time cleanup scanner for MyWork folders.

Discovers files that need triage: Inbox contents, junk extensions
in Source Library, loose files at RFP root.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Files to always skip (infrastructure)
_SKIP_NAMES = frozenset(
    {
        "folder_manifest.yaml",
        "_triage_log.jsonl",
        "_triage_schema.yaml",
        "desktop.ini",
        "thumbs.db",
    }
)

# Directory names to skip entirely
_SKIP_DIRS = frozenset({".corp", "_knowledge", ".git", "__pycache__"})

# Junk extensions to flag in Source Library
_JUNK_EXTENSIONS = frozenset({".url", ".log", ".tmp", ".bak"})


@dataclass
class FileInfo:
    """Info about a file to be triaged."""

    path: Path
    name: str
    extension: str
    size_bytes: int
    current_folder: str  # top-level MyWork folder, e.g. "00_Inbox"
    relative_path: str  # path relative to MyWork root, forward slashes


def _is_infrastructure(name: str) -> bool:
    """Check if file is infrastructure that should never be moved."""
    lower = name.lower()
    return lower in _SKIP_NAMES or lower.startswith(("_triage_", ".corp", "~$"))


def _scan_inbox(mywork_root: Path) -> list[FileInfo]:
    """Scan 00_Inbox for all non-infrastructure files."""
    inbox = mywork_root / "00_Inbox"
    if not inbox.is_dir():
        return []

    results: list[FileInfo] = []
    for entry in sorted(inbox.iterdir()):
        if not entry.is_file():
            continue
        if _is_infrastructure(entry.name):
            continue
        rel = str(entry.relative_to(mywork_root)).replace("\\", "/")
        results.append(
            FileInfo(
                path=entry.resolve(),
                name=entry.name,
                extension=entry.suffix.lower(),
                size_bytes=entry.stat().st_size,
                current_folder="00_Inbox",
                relative_path=rel,
            )
        )
    return results


def _scan_source_library_junk(mywork_root: Path) -> list[FileInfo]:
    """Scan 60_Source_Library for .url, .log, and other junk files."""
    source_lib = mywork_root / "60_Source_Library"
    if not source_lib.is_dir():
        return []

    results: list[FileInfo] = []
    for dirpath, dirnames, filenames in os.walk(source_lib):
        # Prune skip dirs
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
        for fname in filenames:
            if _is_infrastructure(fname):
                continue
            ext = Path(fname).suffix.lower()
            if ext not in _JUNK_EXTENSIONS:
                continue
            filepath = Path(dirpath) / fname
            rel = str(filepath.relative_to(mywork_root)).replace("\\", "/")
            try:
                size = filepath.stat().st_size
            except OSError:
                continue
            results.append(
                FileInfo(
                    path=filepath.resolve(),
                    name=fname,
                    extension=ext,
                    size_bytes=size,
                    current_folder="60_Source_Library",
                    relative_path=rel,
                )
            )
    return results


def _scan_rfp_loose_files(mywork_root: Path) -> list[FileInfo]:
    """Scan 50_RFP root for loose files (not in subfolders)."""
    rfp = mywork_root / "50_RFP"
    if not rfp.is_dir():
        return []

    results: list[FileInfo] = []
    for entry in sorted(rfp.iterdir()):
        if not entry.is_file():
            continue
        if _is_infrastructure(entry.name):
            continue
        rel = str(entry.relative_to(mywork_root)).replace("\\", "/")
        results.append(
            FileInfo(
                path=entry.resolve(),
                name=entry.name,
                extension=entry.suffix.lower(),
                size_bytes=entry.stat().st_size,
                current_folder="50_RFP",
                relative_path=rel,
            )
        )
    return results


def scan_problematic_files(mywork_root: Path) -> list[FileInfo]:
    """Scan MyWork for files that need attention.

    Scans:
    1. All files in 00_Inbox (except infrastructure)
    2. Junk extension files (.url, .log, etc.) in 60_Source_Library
    3. Loose files at root of 50_RFP
    """
    mywork_root = mywork_root.resolve()
    results: list[FileInfo] = []

    results.extend(_scan_inbox(mywork_root))
    log.info("Inbox: %d files to triage", len(results))

    junk_count_before = len(results)
    results.extend(_scan_source_library_junk(mywork_root))
    log.info("Source Library junk: %d files", len(results) - junk_count_before)

    rfp_count_before = len(results)
    results.extend(_scan_rfp_loose_files(mywork_root))
    log.info("RFP loose files: %d files", len(results) - rfp_count_before)

    log.info("Total problematic files: %d", len(results))
    return results
