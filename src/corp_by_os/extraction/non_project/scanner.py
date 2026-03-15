"""Discovers extractable files with path-jailing for security."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Directories to skip during scanning
_SKIP_PREFIXES = (".", "_")


@dataclass
class ScanResult:
    """A file discovered for extraction."""

    absolute_path: Path
    relative_path: str  # relative to scan root, forward slashes
    extension: str
    size_bytes: int


class ScanSecurityError(Exception):
    """Raised when a path escapes the scan jail."""


def _is_inside_jail(path: Path, jail: Path) -> bool:
    """Check that resolved path is inside the jail directory."""
    try:
        path.resolve().relative_to(jail.resolve())
        return True
    except ValueError:
        return False


def _should_skip_dir(name: str) -> bool:
    """Skip directories starting with . or _."""
    return any(name.startswith(p) for p in _SKIP_PREFIXES)


def scan_folder(
    folder_path: Path,
    allow_extensions: list[str],
    recursive: bool = True,
) -> list[ScanResult]:
    """Scan folder for extractable files.

    Security:
    - Resolves all paths to absolute
    - Rejects symlinks whose target falls outside folder_path
    - Skips hidden files/folders (starting with . or _)
    - Skips _knowledge/ directories
    - Respects allow_extensions filter
    """
    folder_path = folder_path.resolve()
    if not folder_path.is_dir():
        log.warning("Scan target is not a directory: %s", folder_path)
        return []

    # Normalize extensions to lowercase with leading dot
    allowed = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in allow_extensions
    }

    results: list[ScanResult] = []

    if recursive:
        walker = os.walk(folder_path)
    else:
        # Single level: just the top directory
        try:
            entries = list(os.scandir(folder_path))
        except OSError as exc:
            log.warning("Cannot scan %s: %s", folder_path, exc)
            return []
        walker = [(str(folder_path), [], [e.name for e in entries if e.is_file()])]

    for dirpath_str, dirnames, filenames in walker:
        dirpath = Path(dirpath_str)

        # Prune skippable directories in-place (modifies os.walk traversal)
        if recursive:
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        # Skip if current dir itself should be skipped (except root)
        if dirpath != folder_path and _should_skip_dir(dirpath.name):
            continue

        for filename in filenames:
            # Skip hidden files
            if any(filename.startswith(p) for p in _SKIP_PREFIXES):
                continue

            filepath = dirpath / filename
            resolved = filepath.resolve()

            # Path jail check
            if not _is_inside_jail(resolved, folder_path):
                log.warning(
                    "Path escapes scan jail, skipping: %s -> %s",
                    filepath,
                    resolved,
                )
                continue

            # Symlink escape check
            if filepath.is_symlink():
                link_target = filepath.resolve()
                if not _is_inside_jail(link_target, folder_path):
                    log.warning(
                        "Symlink escapes jail, skipping: %s -> %s",
                        filepath,
                        link_target,
                    )
                    continue

            # Extension filter
            ext = resolved.suffix.lower()
            if ext not in allowed:
                continue

            # Build relative path with forward slashes
            try:
                rel = resolved.relative_to(folder_path)
            except ValueError:
                continue
            rel_str = str(rel).replace("\\", "/")

            try:
                size = resolved.stat().st_size
            except OSError:
                log.warning("Cannot stat %s, skipping", resolved)
                continue

            results.append(
                ScanResult(
                    absolute_path=resolved,
                    relative_path=rel_str,
                    extension=ext,
                    size_bytes=size,
                )
            )

    results.sort(key=lambda r: r.relative_path)
    return results
