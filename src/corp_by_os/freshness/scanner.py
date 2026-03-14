"""Source-tracking freshness scanner.

Checks vault notes against their source files to detect
staleness, orphaning, and age-based review needs.

Strategy:
1. Parse frontmatter for source_path, source_hash, source_mtime, extracted_at
2. If source file missing -> orphaned
3. If source mtime unchanged -> skip hash (lazy optimization)
4. If source mtime changed -> compute hash -> if different -> stale
5. If extracted > 6 months ago -> review_due
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REVIEW_AGE_DAYS = 180  # 6 months


@dataclass
class FreshnessResult:
    """Result of scanning a single note for freshness."""

    note_path: str
    source_path: str | None
    status: str  # 'fresh' | 'stale' | 'orphaned' | 'review_due' | 'no_source' | 'error'
    reason: str
    source_hash_old: str | None
    source_hash_new: str | None
    source_mtime_old: str | None
    source_mtime_new: str | None
    extracted_at: str | None
    days_since_extraction: int | None


@dataclass
class FreshnessSummary:
    """Summary of freshness scan across all notes."""

    total_scanned: int = 0
    fresh: int = 0
    stale: int = 0
    orphaned: int = 0
    review_due: int = 0
    no_source: int = 0
    errors: int = 0
    results: list[FreshnessResult] = field(default_factory=list)


def compute_hash(filepath: Path) -> str:
    """SHA-256 hash of file content, chunked for large files."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter from a vault note.

    Returns parsed dict or None if frontmatter is missing/invalid.
    """
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None

    try:
        import yaml

        return yaml.safe_load(text[3:end])
    except Exception:
        return None


def _make_result(
    note_path: str,
    source_path: str | None,
    status: str,
    reason: str,
    *,
    source_hash_old: str | None = None,
    source_hash_new: str | None = None,
    source_mtime_old: str | None = None,
    source_mtime_new: str | None = None,
    extracted_at: str | None = None,
    days_since_extraction: int | None = None,
) -> FreshnessResult:
    """Convenience builder for FreshnessResult."""
    return FreshnessResult(
        note_path=note_path,
        source_path=source_path,
        status=status,
        reason=reason,
        source_hash_old=source_hash_old,
        source_hash_new=source_hash_new,
        source_mtime_old=source_mtime_old,
        source_mtime_new=source_mtime_new,
        extracted_at=str(extracted_at) if extracted_at else None,
        days_since_extraction=days_since_extraction,
    )


def _calc_days_since(extracted_at: str | None) -> int | None:
    """Parse extracted_at and return days since extraction."""
    if not extracted_at:
        return None
    try:
        ext_date = datetime.fromisoformat(str(extracted_at))
        return (datetime.now() - ext_date).days
    except (ValueError, TypeError):
        return None


def scan_note_freshness(
    note_path: Path,
    mywork_root: Path,
) -> FreshnessResult:
    """Check a single vault note against its source file.

    Lazy hashing: only computes hash when mtime has changed,
    avoiding unnecessary I/O on large source files.
    """
    note_str = str(note_path)

    fm = parse_frontmatter(note_path)
    if fm is None:
        return _make_result(
            note_str, None, "error", "Cannot parse frontmatter",
        )

    source_path_str = fm.get("source_path") or fm.get("source")
    if not source_path_str:
        return _make_result(
            note_str, None, "no_source",
            "No source_path in frontmatter (legacy v1 note)",
            extracted_at=fm.get("extracted_at"),
        )

    # Resolve source path (relative paths resolve against mywork_root)
    source_file = Path(str(source_path_str))
    if not source_file.is_absolute():
        source_file = mywork_root / str(source_path_str)

    source_hash_old = fm.get("source_hash")
    source_mtime_old = fm.get("source_mtime")
    extracted_at = fm.get("extracted_at") or fm.get("date")
    days_since = _calc_days_since(extracted_at)

    common = dict(
        source_hash_old=source_hash_old,
        source_mtime_old=str(source_mtime_old) if source_mtime_old else None,
        extracted_at=extracted_at,
        days_since_extraction=days_since,
    )

    # Check if source exists
    if not source_file.exists():
        return _make_result(
            note_str, str(source_path_str),
            "orphaned", f"Source file missing: {source_path_str}",
            **common,
        )

    # Get current mtime
    current_mtime = datetime.fromtimestamp(
        source_file.stat().st_mtime,
    ).isoformat()
    common["source_mtime_new"] = current_mtime

    # Lazy: if mtime unchanged, skip hash
    if source_mtime_old and current_mtime == str(source_mtime_old):
        if days_since and days_since > REVIEW_AGE_DAYS:
            return _make_result(
                note_str, str(source_path_str),
                "review_due",
                f"Extracted {days_since} days ago (>{REVIEW_AGE_DAYS}d threshold)",
                **common,
            )
        return _make_result(
            note_str, str(source_path_str),
            "fresh", "Source unchanged (mtime match)",
            **common,
        )

    # mtime changed — compute hash
    try:
        current_hash = compute_hash(source_file)
    except Exception as exc:
        return _make_result(
            note_str, str(source_path_str),
            "error", f"Cannot hash source: {exc}",
            **common,
        )
    common["source_hash_new"] = current_hash

    if source_hash_old and current_hash != source_hash_old:
        return _make_result(
            note_str, str(source_path_str),
            "stale", "Source file content changed (hash mismatch)",
            **common,
        )

    # Hash matches or no old hash — check age
    if days_since and days_since > REVIEW_AGE_DAYS:
        return _make_result(
            note_str, str(source_path_str),
            "review_due", f"Extracted {days_since} days ago",
            **common,
        )

    return _make_result(
        note_str, str(source_path_str),
        "fresh", "Source unchanged",
        **common,
    )


def scan_vault_freshness(
    vault_root: Path,
    mywork_root: Path,
) -> FreshnessSummary:
    """Scan all vault notes for freshness.

    Scans 02_sources/ and 04_evergreen/ directories.
    """
    summary = FreshnessSummary()

    scan_dirs = [
        vault_root / "02_sources",
        vault_root / "04_evergreen",
    ]

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for md_file in scan_dir.rglob("*.md"):
            result = scan_note_freshness(md_file, mywork_root)
            summary.total_scanned += 1
            summary.results.append(result)

            match result.status:
                case "fresh":
                    summary.fresh += 1
                case "stale":
                    summary.stale += 1
                case "orphaned":
                    summary.orphaned += 1
                case "review_due":
                    summary.review_due += 1
                case "no_source":
                    summary.no_source += 1
                case "error":
                    summary.errors += 1

    return summary
