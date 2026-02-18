"""
Phase 1: Archive Copy

Copies old role folders from OneDrive root to MyWork/Archive_* structure.
No renaming — files copied exactly as-is.

Usage:
    python scripts/phase1_archive_copy.py           # dry-run (default, safe)
    python scripts/phase1_archive_copy.py --execute  # actually copy files

Reads OneDrive path from CORP_ONEDRIVE_PATH env var (or .env).
"""

import argparse
import shutil
import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings


# ---------------------------------------------------------------------------
# Migration map: (source relative to OneDrive, destination relative to MyWork)
# ---------------------------------------------------------------------------
ARCHIVE_MAP = [
    ("Projects/_Academy 2020 TC",        "Archive_SalesAcademy/Academy_2020_TC"),
    ("Projects/_Academy 2021 Mentor",    "Archive_SalesAcademy/Academy_2021_Mentor"),
    ("Projects/_Academy 2022 Sales",     "Archive_SalesAcademy/Academy_2022_Sales"),
    ("Projects/_Inbound BDR",            "Archive_BDR"),
    ("Projects/_Technical Consultant",   "Archive_TechnicalConsultant"),
    ("Projects/_BY Extra Initiatives",   "Archive_ExtraInitiatives"),
    ("Projects/_BY Admin",               "Archive_Admin"),
    ("Projects/Marketing",               "Archive_ExtraInitiatives/Marketing"),
    ("Pictures/Camera Roll",             "Camera_Roll"),
    ("Recordings",                       "Archive_TechnicalConsultant/Recordings"),
]


def count_files(path: Path) -> int:
    """Count all files recursively under path."""
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*") if _.is_file())


def copy_tree(src: Path, dst: Path, dry_run: bool) -> tuple[int, int]:
    """
    Copy src directory tree to dst.

    Returns (copied_count, skipped_count).
    Skips files that already exist at destination with same size.
    """
    copied = 0
    skipped = 0

    for src_file in src.rglob("*"):
        if not src_file.is_file():
            continue

        relative = src_file.relative_to(src)
        dst_file = dst / relative

        # Skip if already copied (same size = good enough check)
        if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
            skipped += 1
            continue

        if dry_run:
            print(f"  [DRY] {src_file.name}  →  {dst_file}")
        else:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            print(f"  COPY  {src_file.name}  →  {dst_file}")

        copied += 1

    return copied, skipped


def run(dry_run: bool) -> None:
    settings = get_settings()
    onedrive = settings.onedrive_path
    mywork = onedrive / "MyWork"

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"\n{'='*60}")
    print(f"Phase 1: Archive Copy  [{mode}]")
    print(f"OneDrive : {onedrive}")
    print(f"MyWork   : {mywork}")
    print(f"{'='*60}\n")

    if not onedrive.exists():
        print(f"ERROR: OneDrive path does not exist: {onedrive}")
        print("Set CORP_ONEDRIVE_PATH in your .env file.")
        sys.exit(1)

    if not dry_run:
        mywork.mkdir(parents=True, exist_ok=True)

    total_copied = 0
    total_skipped = 0
    missing_sources = []

    for src_rel, dst_rel in ARCHIVE_MAP:
        src = onedrive / src_rel
        dst = mywork / dst_rel

        file_count = count_files(src)

        if not src.exists():
            print(f"SKIP (not found): {src_rel}  [{file_count} files]")
            missing_sources.append(src_rel)
            continue

        print(f"\n{'─'*50}")
        print(f"SOURCE: {src_rel}  ({file_count} files)")
        print(f"DEST  : MyWork/{dst_rel}")

        copied, skipped = copy_tree(src, dst, dry_run=dry_run)
        total_copied += copied
        total_skipped += skipped

        print(f"  → {copied} to copy, {skipped} already present")

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY [{mode}]")
    print(f"  Files to copy   : {total_copied}")
    print(f"  Already present : {total_skipped}")
    if missing_sources:
        print(f"  Missing sources : {len(missing_sources)}")
        for s in missing_sources:
            print(f"    - {s}")
    print(f"{'='*60}")

    if dry_run:
        print("\nThis was a dry-run. No files were copied.")
        print("Run with --execute to perform the actual copy.")


def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Copy archive folders to MyWork structure."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually copy files (default is dry-run)",
    )
    args = parser.parse_args()

    run(dry_run=not args.execute)


if __name__ == "__main__":
    main()
