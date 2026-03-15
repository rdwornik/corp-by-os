"""Execute approved moves from moves.yaml.

Only acts on entries with approved: true. Skips everything else.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Summary of move execution."""

    moved: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0


def execute_moves(
    moves_path: Path,
    mywork_root: Path,
    dry_run: bool = False,
) -> ExecutionResult:
    """Execute approved moves from moves.yaml.

    Only executes entries where approved is explicitly true.
    Skips approved: false, approved: null, or missing approved field.
    """
    mywork_root = mywork_root.resolve()

    with open(moves_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    entries = data.get("moves", [])
    result = ExecutionResult()

    for entry in entries:
        approved = entry.get("approved")
        if approved is not True:
            result.skipped += 1
            continue

        source_rel = entry.get("source", "")
        action = entry.get("action", "keep")
        dest_folder = entry.get("destination", "")
        proposed_name = entry.get("proposed_name", "")

        source = mywork_root / source_rel.replace("/", "\\")
        if not source.exists():
            log.warning("Source not found, skipping: %s", source_rel)
            result.failed += 1
            continue

        if action == "delete":
            if dry_run:
                log.info("[DRY RUN] Would delete: %s", source_rel)
            else:
                source.unlink()
                log.info("Deleted: %s", source_rel)
            result.deleted += 1

        elif action == "move":
            dest_dir = mywork_root / dest_folder.replace("/", "\\")
            dest_file = dest_dir / (proposed_name or source.name)

            if dry_run:
                log.info(
                    "[DRY RUN] Would move: %s -> %s/%s",
                    source_rel,
                    dest_folder,
                    proposed_name or source.name,
                )
            else:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(dest_file))
                log.info(
                    "Moved: %s -> %s/%s",
                    source_rel,
                    dest_folder,
                    proposed_name or source.name,
                )
            result.moved += 1

        elif action == "keep":
            result.skipped += 1

        else:
            log.warning("Unknown action '%s' for %s", action, source_rel)
            result.skipped += 1

    return result
