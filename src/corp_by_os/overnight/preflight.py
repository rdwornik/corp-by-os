"""Pre-flight checks before overnight run.

Returns a list of human-readable errors. Empty list = all OK.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def run_preflight(
    mywork_root: Path,
    vault_root: Path,
    app_data_path: Path,
) -> list[str]:
    """Run all pre-flight checks.

    Checks:
    1. GEMINI_API_KEY is set and non-empty
    2. Vault path exists and is writable
    3. MyWork path exists
    4. routing_map.yaml exists and parses
    5. Disk space > 5GB free on vault drive
    6. No stale lock files in .corp/state/

    Returns list of errors (empty = all OK).
    """
    errors: list[str] = []

    # 1. API key
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        errors.append("GEMINI_API_KEY not set. Load CKE .env or set the variable.")

    # 2. Vault
    if not vault_root.exists():
        errors.append(f"Vault path does not exist: {vault_root}")
    elif not os.access(str(vault_root), os.W_OK):
        errors.append(f"Vault path is not writable: {vault_root}")

    # 3. MyWork
    if not mywork_root.exists():
        errors.append(f"MyWork root does not exist: {mywork_root}")

    # 4. Routing map
    routing_map_path = mywork_root / "90_System" / "routing_map.yaml"
    if not routing_map_path.exists():
        errors.append(f"routing_map.yaml not found: {routing_map_path}")
    else:
        try:
            with open(routing_map_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                errors.append("routing_map.yaml is not a valid YAML dict")
        except yaml.YAMLError as exc:
            errors.append(f"routing_map.yaml parse error: {exc}")

    # 5. Disk space (5GB minimum on vault drive)
    try:
        usage = shutil.disk_usage(str(vault_root))
        free_gb = usage.free / (1024**3)
        if free_gb < 5.0:
            errors.append(f"Low disk space on vault drive: {free_gb:.1f}GB free (need 5GB+)")
    except OSError as exc:
        errors.append(f"Cannot check disk space: {exc}")

    # 6. Stale lock files
    lock_dir = app_data_path / "state"
    if lock_dir.exists():
        for lock_file in lock_dir.glob("*.lock"):
            errors.append(
                f"Stale lock file found: {lock_file} — delete if no other overnight run is active"
            )

    return errors
