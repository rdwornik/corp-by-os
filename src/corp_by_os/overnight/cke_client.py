"""Thin wrapper around CKE — direct import, no subprocess.

Imports CKE's BatchJobRunner (batch API) and BatchProcessor (sync)
directly. Falls back to subprocess with env passthrough if import fails.

CKE path is resolved from agents.yaml via AppConfig.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_cke_imported = False
_import_error: str | None = None


def _get_cke_path() -> Path:
    """Resolve CKE repo path from agents.yaml config."""
    from corp_by_os.config import get_config

    cfg = get_config()
    agent = cfg.agents.get("corp-knowledge-extractor", {})
    path = agent.get("path", "")
    if not path:
        raise RuntimeError("corp-knowledge-extractor path not set in agents.yaml")
    return Path(path)


def _ensure_cke_importable() -> None:
    """Add CKE src to sys.path and load its .env for API keys."""
    global _cke_imported, _import_error

    if _cke_imported:
        return

    cke_root = _get_cke_path()
    cke_src = str(cke_root / "src")

    if cke_src not in sys.path:
        sys.path.insert(0, cke_src)
        logger.debug("Added CKE to sys.path: %s", cke_src)

    # Load CKE's .env for GEMINI_API_KEY
    env_path = cke_root / ".env"
    if env_path.exists():
        from dotenv import dotenv_values

        for k, v in dotenv_values(env_path).items():
            if v and k not in os.environ:
                os.environ[k] = v
        logger.debug("Loaded CKE .env from %s", env_path)

    # Verify import works
    try:
        import batch_api  # noqa: F401
        import manifest  # noqa: F401

        _cke_imported = True
        logger.info("CKE direct import OK from %s", cke_src)
    except ImportError as exc:
        _import_error = str(exc)
        _cke_imported = False
        logger.warning("CKE direct import failed: %s", exc)


def is_available() -> tuple[bool, str]:
    """Check if CKE is importable. Returns (ok, error_message)."""
    try:
        _ensure_cke_importable()
    except RuntimeError as exc:
        return False, str(exc)
    if not _cke_imported:
        return False, _import_error or "Unknown import error"
    return True, ""


def load_cke_config() -> dict[str, Any]:
    """Load CKE's own configuration (settings, processing, etc.)."""
    _ensure_cke_importable()
    from config.config_loader import load_config  # type: ignore[import-untyped]

    # CKE's load_config expects to be run from CKE's root
    original_cwd = os.getcwd()
    try:
        os.chdir(str(_get_cke_path()))
        return load_config()
    finally:
        os.chdir(original_cwd)


def estimate_cost(manifest_path: Path) -> dict[str, Any]:
    """Estimate extraction cost without running anything.

    Returns: {total_cost, tier_breakdown: {1: n, 2: n, 3: n}, file_count}
    """
    _ensure_cke_importable()
    from inventory import FileType, SourceFile  # type: ignore[import-untyped]
    from manifest import Manifest  # type: ignore[import-untyped]
    from tier_router import estimate_batch_cost  # type: ignore[import-untyped]

    m = Manifest.from_file(manifest_path)
    source_files = []
    for entry in m.files:
        ft = (
            FileType(entry.doc_type)
            if entry.doc_type in FileType.__members__.values()
            else FileType.DOCUMENT
        )
        source_files.append(
            SourceFile(
                path=entry.path,
                type=ft,
                size_bytes=entry.path.stat().st_size if entry.path.exists() else 0,
                name=entry.name or entry.id,
            )
        )

    return estimate_batch_cost(source_files)


def extract_batch(
    manifest_path: Path,
    model: str | None = None,
    poll_interval: int = 60,
    timeout: int = 28800,
    resume: bool = True,
) -> dict[str, Any]:
    """Submit manifest to Gemini Batch API via CKE's BatchJobRunner.

    Args:
        manifest_path: Path to CKE-compatible manifest JSON.
        model: Gemini model override (None = use CKE default).
        poll_interval: Seconds between batch status checks.
        timeout: Max wait time in seconds (default 8h).
        resume: Skip already-completed files.

    Returns: {total, done, error, skipped, cost, tiers}
    """
    _ensure_cke_importable()
    from batch_api import BatchJobRunner  # type: ignore[import-untyped]
    from manifest import Manifest  # type: ignore[import-untyped]

    config = load_cke_config()
    if model:
        config["model_override"] = model

    m = Manifest.from_file(manifest_path)
    runner = BatchJobRunner(
        manifest=m,
        config=config,
        resume=resume,
    )

    logger.info(
        "Starting batch extraction: %d files, poll=%ds, timeout=%ds",
        len(m.files),
        poll_interval,
        timeout,
    )
    return runner.run(poll_interval=poll_interval, timeout=timeout)


def extract_sync(
    manifest_path: Path,
    model: str | None = None,
    max_rpm: int = 80,
    resume: bool = True,
) -> dict[str, Any]:
    """Synchronous extraction via CKE's BatchProcessor (fallback).

    Args:
        manifest_path: Path to CKE-compatible manifest JSON.
        model: Gemini model override.
        max_rpm: Max requests per minute.
        resume: Skip already-completed files.

    Returns: {total, done, error, skipped, cost, tiers}
    """
    _ensure_cke_importable()
    from batch import BatchProcessor  # type: ignore[import-untyped]
    from manifest import Manifest  # type: ignore[import-untyped]

    config = load_cke_config()
    if model:
        config["model_override"] = model

    m = Manifest.from_file(manifest_path)
    processor = BatchProcessor(
        manifest=m,
        config=config,
        max_rpm=max_rpm,
        resume=resume,
    )

    logger.info(
        "Starting sync extraction: %d files, max_rpm=%d",
        len(m.files),
        max_rpm,
    )
    return processor.process_all()


def scan_local(
    path: Path,
    recursive: bool = True,
    exclude: tuple[str, ...] = (
        "80_Archive",
        ".corp",
        "_knowledge",
        ".venv",
        "__pycache__",
        ".git",
    ),
) -> list[dict]:
    """Run CKE Tier 1 local scan — no API calls, fully local.

    Scans files and extracts metadata (title, hash, text preview, tier)
    using CKE's scan module directly.

    Args:
        path: Directory to scan.
        recursive: Scan subdirectories.
        exclude: Folder names to skip.

    Returns:
        List of FileScanResult dicts with keys:
        path, filename, extension, size_bytes, file_hash, tier, metadata, error
    """
    _ensure_cke_importable()
    from dataclasses import asdict

    from scan import scan_path  # type: ignore[import-untyped]

    results = scan_path(path, recursive=recursive, exclude=exclude)
    return [asdict(r) for r in results]
