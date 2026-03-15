"""Atomic vault write -- moves extraction output from staging to vault.

Corp-by-os is the sole vault writer for non-project extraction.
All vault writes go through move_to_vault().
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)


def _file_hash(path: Path) -> str:
    """Compute SHA-256 of a file for identity comparison."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def move_to_vault(
    staging_dir: Path,
    vault_root: Path,
    vault_target: str,
) -> int:
    """Move extracted packages from staging to vault target.

    CKE writes output as: staging_dir/{entry_id}/extract/*.md, *.json, _meta.yaml
    We move the full package directories to vault_root/vault_target/.

    Returns count of files moved.
    """
    staging_dir = staging_dir.resolve()
    dest_root = (vault_root / vault_target).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)

    moved = 0

    for item in sorted(staging_dir.iterdir()):
        if not item.is_dir():
            continue
        # Each item is a package dir (named by entry.id)
        dest_pkg = dest_root / item.name

        if dest_pkg.exists():
            # Merge: walk source package and move individual files
            for src_file in sorted(item.rglob("*")):
                if not src_file.is_file():
                    continue
                rel = src_file.relative_to(item)
                dst_file = dest_pkg / rel

                if dst_file.exists() and _file_hash(src_file) == _file_hash(dst_file):
                    log.debug(
                        "Skipping identical: %s",
                        str(rel).replace("\\", "/"),
                    )
                    continue

                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_file), str(dst_file))
                log.info(
                    "Updated: %s -> %s",
                    str(rel).replace("\\", "/"),
                    str(dst_file).replace("\\", "/"),
                )
                moved += 1
        else:
            # New package: move entire directory
            shutil.move(str(item), str(dest_pkg))
            file_count = sum(1 for _ in dest_pkg.rglob("*") if _.is_file())
            log.info(
                "Moved package: %s (%d files)",
                item.name,
                file_count,
            )
            moved += file_count

    return moved
