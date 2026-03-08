"""
Phase 1: Archive Copy

Copies old role folders from OneDrive root to MyWork/Archive_* structure.
No renaming -- files copied exactly as-is, with three exceptions:

  - Large project artifact folders are ZIPped instead of copied file-by-file.
  - Recordings are split by year: <=2022 -> archive, >=2024 -> inbox.
  - Academy folders with 0 files are flagged as cloud-only and skipped.

Usage:
    python scripts/phase1_archive_copy.py            # dry-run (default, safe)
    python scripts/phase1_archive_copy.py --execute  # actually copy/zip files

Reads OneDrive path from CORP_ONEDRIVE_PATH env var (or .env).
"""

import argparse
import datetime
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

COPY_MAP = [
    ("Projects/_Academy 2020 TC",      "Archive_SalesAcademy/Academy_2020_TC"),
    ("Projects/_Academy 2021 Mentor",  "Archive_SalesAcademy/Academy_2021_Mentor"),
    ("Projects/_Academy 2022 Sales",   "Archive_SalesAcademy/Academy_2022_Sales"),
    ("Projects/_Inbound BDR",          "Archive_BDR"),
    ("Projects/_Technical Consultant", "Archive_TechnicalConsultant"),
    ("Projects/_BY Extra Initiatives", "Archive_ExtraInitiatives"),
    ("Projects/_BY Admin",             "Archive_Admin"),
    ("Projects/Marketing",             "Archive_ExtraInitiatives/Marketing"),
    ("Pictures/Camera Roll",           "Camera_Roll"),
]

ACADEMY_FOLDERS = {
    "Projects/_Academy 2020 TC",
    "Projects/_Academy 2021 Mentor",
    "Projects/_Academy 2022 Sales",
}

# Subtrees to ZIP rather than copy
ZIP_MAP = [
    (
        "Projects/_Technical Consultant/_LCT/Diageo",
        "Archive_TechnicalConsultant/LCT_Diageo_Archive.zip",
    ),
    (
        "Projects/_Technical Consultant/_PSA Project/Code",
        "Archive_TechnicalConsultant/PSA_Project_Code_Archive.zip",
    ),
    (
        "Projects/_BY Extra Initiatives/TMS Translation/trans-tms-by",
        "Archive_ExtraInitiatives/TMS_Translation_Archive.zip",
    ),
]

# Source paths that contain ZIP subtrees and need per-file exclusion
FOLDERS_WITH_ZIP_EXCLUSIONS = {
    "Projects/_Technical Consultant",
    "Projects/_BY Extra Initiatives",
}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    copied: int = 0
    skipped: int = 0
    zips_done: int = 0
    zipped_files: int = 0
    warnings: list[str] = field(default_factory=list)
    cloud_only: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


def rel(path: Path, base: Path) -> str:
    """Return path relative to base as a forward-slash string."""
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def is_zip_excluded(file: Path, onedrive: Path) -> bool:
    """Return True if file is inside one of the ZIP_MAP subtrees."""
    file_rel = rel(file, onedrive)
    return any(file_rel.startswith(src.replace("\\", "/")) for src, _ in ZIP_MAP)


def lp(path: Path) -> str:
    """Return Windows extended-length path string (bypasses 260-char MAX_PATH)."""
    return "\\\\?\\" + os.path.abspath(str(path))


def safe_copy(src: Path, dst: Path, stats: Stats) -> str:
    """
    Copy src -> dst with full Windows long-path support.

    Returns one of: 'ok', 'cloud', 'fail'.
    - 'cloud'  : WinError 389 — file not downloaded from OneDrive yet.
    - 'fail'   : any other OS error (logged to stats.warnings).
    """
    try:
        os.makedirs(lp(dst.parent), exist_ok=True)
        shutil.copy2(lp(src), lp(dst))
        return "ok"
    except OSError as e:
        if getattr(e, "winerror", None) == 389:
            stats.cloud_only.append(str(src.name))
            return "cloud"
        stats.warnings.append(f"[FAIL] {src.name}: {e}")
        return "fail"


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------

def copy_tree(
    src: Path,
    dst: Path,
    onedrive: Path,
    mywork: Path,
    dry_run: bool,
    stats: Stats,
    exclude_zip_subtrees: bool = False,
) -> None:
    """
    Copy src -> dst recursively.

    Dry-run: prints per-folder summary only.
    Execute: prints each file as it is copied.
    Skips files already present at destination with matching size.
    """
    to_copy = []
    to_skip = 0

    for src_file in sorted(src.rglob("*")):
        if not src_file.is_file():
            continue
        if exclude_zip_subtrees and is_zip_excluded(src_file, onedrive):
            continue

        relative = src_file.relative_to(src)
        dst_file  = dst / relative

        if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
            to_skip += 1
        else:
            to_copy.append((src_file, dst_file, relative))

    if dry_run:
        print(f"    {len(to_copy)} files to copy, {to_skip} already present")
    else:
        for src_file, dst_file, relative in to_copy:
            dst_rel = rel(dst_file, mywork)
            result  = safe_copy(src_file, dst_file, stats)
            if result == "ok":
                print(f"  [COPY]  {src_file.name}  ->  MyWork/{dst_rel}")
            elif result == "cloud":
                print(f"  [CLOUD] {src_file.name}  (not downloaded)")
            else:
                print(f"  [FAIL]  {src_file.name}  (see WARNINGS)")

    stats.copied  += len(to_copy)
    stats.skipped += to_skip


# ---------------------------------------------------------------------------
# ZIP
# ---------------------------------------------------------------------------

def zip_folder(
    src: Path,
    dst_zip: Path,
    onedrive: Path,
    mywork: Path,
    dry_run: bool,
    stats: Stats,
) -> None:
    """ZIP src directory into dst_zip."""
    n       = count_files(src)
    src_rel = rel(src, onedrive)
    dst_rel = rel(dst_zip, mywork)

    if dst_zip.exists():
        size_mb = dst_zip.stat().st_size // 1024 // 1024
        print(f"  [SKIP] {dst_rel} already exists ({size_mb} MB)")
        return

    print(f"  [ZIP]  {src_rel}  ({n} files)  ->  MyWork/{dst_rel}")
    stats.zips_done    += 1
    stats.zipped_files += n

    if not dry_run:
        dst_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(src))


# ---------------------------------------------------------------------------
# Recordings split
# ---------------------------------------------------------------------------

def copy_recordings(
    src: Path,
    mywork: Path,
    dry_run: bool,
    stats: Stats,
) -> None:
    """
    Copy recordings split by year:
      <= 2022  ->  Archive_TechnicalConsultant/Recordings/
      >= 2024  ->  00_Tech_PreSales/00_Inbox/recordings/
    """
    dst_archive = mywork / "Archive_TechnicalConsultant" / "Recordings"
    dst_inbox   = mywork / "00_Tech_PreSales" / "00_Inbox" / "recordings"

    archive_files = []
    inbox_files   = []

    for src_file in sorted(src.rglob("*")):
        if not src_file.is_file():
            continue

        year = datetime.datetime.fromtimestamp(src_file.stat().st_mtime).year

        if year <= 2022:
            dst_file = dst_archive / src_file.name
            bucket   = archive_files
        else:
            dst_file = dst_inbox / src_file.name
            bucket   = inbox_files

        if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
            stats.skipped += 1
        else:
            bucket.append((src_file, dst_file, year))

    if dry_run:
        print(f"    <= 2022 (archive): {len(archive_files)} files")
        for f, _, yr in archive_files:
            print(f"      [COPY] {yr}  {f.name}  ->  Archive_TechnicalConsultant/Recordings/")
        print(f"    >= 2024 (inbox):   {len(inbox_files)} files")
        for f, _, yr in inbox_files:
            print(f"      [COPY] {yr}  {f.name}  ->  00_Tech_PreSales/00_Inbox/recordings/")
    else:
        for src_file, dst_file, yr in archive_files + inbox_files:
            label  = rel(dst_file, mywork)
            result = safe_copy(src_file, dst_file, stats)
            if result == "ok":
                print(f"  [COPY]  {src_file.name}  ({yr})  ->  MyWork/{label}")
            elif result == "cloud":
                print(f"  [CLOUD] {src_file.name}  (not downloaded)")
            else:
                print(f"  [FAIL]  {src_file.name}  (see WARNINGS)")

    stats.copied += len(archive_files) + len(inbox_files)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(dry_run: bool) -> None:
    settings = get_settings()
    onedrive = settings.onedrive_path
    mywork   = onedrive / "MyWork"
    stats    = Stats()

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"\n{'='*60}")
    print(f"Phase 1: Archive Copy  [{mode}]")
    print(f"OneDrive : {onedrive}")
    print(f"MyWork   : {mywork}")
    print(f"{'='*60}")

    if not onedrive.exists():
        print(f"\nERROR: OneDrive path not found: {onedrive}")
        print("Set CORP_ONEDRIVE_PATH in your .env file.")
        sys.exit(1)

    if not dry_run:
        mywork.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # ZIP operations
    # ------------------------------------------------------------------
    print(f"\n{'-'*60}")
    print("=== ZIP OPERATIONS ===\n")

    for src_rel, dst_rel in ZIP_MAP:
        src     = onedrive / src_rel
        dst_zip = mywork / dst_rel

        if not src.exists():
            msg = f"[WARN] ZIP source not found: {src_rel}"
            print(f"  {msg}")
            stats.warnings.append(msg)
            continue

        zip_folder(src, dst_zip, onedrive, mywork, dry_run, stats)

    # ------------------------------------------------------------------
    # Copy operations
    # ------------------------------------------------------------------
    print(f"\n{'-'*60}")
    print("=== COPY OPERATIONS ===\n")

    for src_rel, dst_rel in COPY_MAP:
        src    = onedrive / src_rel
        dst    = mywork / dst_rel
        has_exclusions = src_rel in FOLDERS_WITH_ZIP_EXCLUSIONS

        # Academy cloud-only check
        if src_rel in ACADEMY_FOLDERS:
            n = count_files(src)
            if n == 0:
                msg = f"[WARN] {src_rel} -- 0 files, skipping (cloud-only?). Open in Explorer first."
                print(f"  {msg}")
                stats.warnings.append(msg)
                continue

        if not src.exists():
            msg = f"[WARN] Source not found: {src_rel}"
            print(f"  {msg}")
            stats.warnings.append(msg)
            continue

        # Effective file count (minus files going to ZIP)
        total_n = count_files(src)
        zip_n   = sum(count_files(onedrive / zs) for zs, _ in ZIP_MAP if zs.startswith(src_rel))
        eff_n   = total_n - zip_n

        print(f"  {src_rel}  ({eff_n} files)  ->  MyWork/{dst_rel}")
        copy_tree(src, dst, onedrive, mywork, dry_run, stats, exclude_zip_subtrees=has_exclusions)

    # ------------------------------------------------------------------
    # Recordings
    # ------------------------------------------------------------------
    print(f"\n{'-'*60}")
    print("=== RECORDINGS (split by year) ===\n")

    rec_src = onedrive / "Recordings"
    if rec_src.exists():
        copy_recordings(rec_src, mywork, dry_run, stats)
    else:
        msg = "[WARN] Recordings/ not found"
        print(f"  {msg}")
        stats.warnings.append(msg)

    # ------------------------------------------------------------------
    # Cloud-only report
    # ------------------------------------------------------------------
    if stats.cloud_only:
        print(f"\n{'-'*60}")
        print("=== CLOUD-ONLY FILES (not downloaded) ===\n")
        for name in stats.cloud_only:
            print(f"  [CLOUD] {name}")
        print(f"\n  {len(stats.cloud_only)} files skipped (cloud-only).")
        print("  Open them in Explorer first to download, then re-run.")

    # ------------------------------------------------------------------
    # Warnings
    # ------------------------------------------------------------------
    if stats.warnings:
        print(f"\n{'-'*60}")
        print("=== WARNINGS ===\n")
        for w in stats.warnings:
            print(f"  {w}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'-'*60}")
    print(f"=== SUMMARY [{mode}] ===\n")
    print(f"  Files copied     : {stats.copied}")
    print(f"  Already present  : {stats.skipped}")
    print(f"  Cloud-only skip  : {len(stats.cloud_only)}")
    print(f"  ZIPs to create   : {stats.zips_done}  ({stats.zipped_files} files inside)")
    print(f"  Warnings         : {len(stats.warnings)}")
    print(f"\n{'='*60}")

    if dry_run:
        print("\nDry-run complete. No files were modified.")
        print("Run with --execute to perform the actual operations.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Copy/ZIP archive folders into MyWork structure."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually copy/zip files (default is dry-run)",
    )
    args = parser.parse_args()
    run(dry_run=not args.execute)


if __name__ == "__main__":
    main()
