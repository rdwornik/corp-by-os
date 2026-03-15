"""Duplicate detection using CKE scan results.

Two-layer dedup:
1. Exact: SHA-256 hash grouping (identical files)
2. Near: title + text preview similarity (probable duplicates for review)

Never auto-deletes — only flags for review.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    """Group of files that are duplicates."""

    canonical: dict
    duplicates: list[dict] = field(default_factory=list)
    match_type: str = "exact_hash"  # "exact_hash" or "near_duplicate"
    similarity: float = 1.0

    @property
    def total_wasted_bytes(self) -> int:
        return sum(d.get("size_bytes", 0) for d in self.duplicates)


def deduplicate(
    scan_results: list[dict],
    near_title_threshold: float = 0.90,
    near_text_threshold: float = 0.85,
) -> tuple[list[dict], list[DuplicateGroup]]:
    """Two-layer dedup on CKE scan results.

    Layer 1 — Exact hash:
        Group by SHA-256. Same hash = identical file.
        Keep newest (by mtime) or largest as canonical.

    Layer 2 — Near duplicate:
        Compare title + first 500 chars text preview.
        Flag as probable duplicate if both above threshold.
        Does NOT remove — only flags for review.

    Args:
        scan_results: List of CKE FileScanResult dicts (from scan_path).
        near_title_threshold: SequenceMatcher ratio threshold for titles.
        near_text_threshold: SequenceMatcher ratio threshold for text preview.

    Returns:
        (unique_files, duplicate_groups)
    """
    groups: list[DuplicateGroup] = []

    # --- Layer 1: Exact hash ---
    by_hash: dict[str, list[dict]] = defaultdict(list)
    unhashed: list[dict] = []

    for sr in scan_results:
        h = sr.get("file_hash")
        if h:
            by_hash[h].append(sr)
        else:
            unhashed.append(sr)

    unique_after_exact: list[dict] = list(unhashed)

    for _file_hash, members in by_hash.items():
        if len(members) == 1:
            unique_after_exact.append(members[0])
            continue

        canonical = _select_canonical(members)
        dupes = [m for m in members if m is not canonical]
        groups.append(
            DuplicateGroup(
                canonical=canonical,
                duplicates=dupes,
                match_type="exact_hash",
                similarity=1.0,
            )
        )
        unique_after_exact.append(canonical)
        logger.info(
            "Exact duplicate group: %s (%d copies)",
            canonical.get("filename", "?"),
            len(dupes),
        )

    # --- Layer 2: Near duplicate (title + text) ---
    near_groups = _find_near_duplicates(
        unique_after_exact,
        near_title_threshold,
        near_text_threshold,
    )
    groups.extend(near_groups)

    # Near dupes are flagged but NOT removed from unique list
    # (user must review and decide)

    logger.info(
        "Dedup: %d input → %d unique, %d exact groups, %d near-dupe groups",
        len(scan_results),
        len(unique_after_exact),
        sum(1 for g in groups if g.match_type == "exact_hash"),
        len(near_groups),
    )

    return unique_after_exact, groups


def _select_canonical(group: list[dict]) -> dict:
    """Select best version from duplicate group.

    Priority: newest mtime > larger file > .pptx over .pdf > shorter path.
    """

    def sort_key(item: dict) -> tuple:
        path = Path(item.get("path", ""))
        # Prefer certain extensions
        ext_rank = {".pptx": 0, ".docx": 1, ".xlsx": 2, ".pdf": 3}.get(
            item.get("extension", ""),
            5,
        )
        size = item.get("size_bytes", 0)
        # Shorter path = more accessible
        path_len = len(str(path))
        return (-size, ext_rank, path_len)

    return min(group, key=sort_key)


def _find_near_duplicates(
    files: list[dict],
    title_threshold: float,
    text_threshold: float,
) -> list[DuplicateGroup]:
    """Find near-duplicates by title + text similarity.

    O(n²) comparison — acceptable for ~3000 files with early exits.
    """
    groups: list[DuplicateGroup] = []
    flagged: set[int] = set()

    # Build index of files that have titles
    titled: list[tuple[int, str, str]] = []
    for i, f in enumerate(files):
        meta = f.get("metadata", {})
        title = meta.get("title", "") or ""
        text = (meta.get("text_preview", "") or "")[:500]
        if title:
            titled.append((i, title.lower(), text.lower()))

    for a_idx in range(len(titled)):
        if titled[a_idx][0] in flagged:
            continue

        i_a, title_a, text_a = titled[a_idx]
        near_dupes: list[dict] = []

        for b_idx in range(a_idx + 1, len(titled)):
            if titled[b_idx][0] in flagged:
                continue

            i_b, title_b, text_b = titled[b_idx]

            # Quick length check — very different lengths can't be similar
            if abs(len(title_a) - len(title_b)) > max(len(title_a), len(title_b)) * 0.5:
                continue

            title_sim = SequenceMatcher(None, title_a, title_b).ratio()
            if title_sim < title_threshold:
                continue

            # Title is similar — check text if available
            if text_a and text_b:
                text_sim = SequenceMatcher(None, text_a, text_b).ratio()
                if text_sim < text_threshold:
                    continue
                sim = (title_sim + text_sim) / 2
            else:
                sim = title_sim

            near_dupes.append(files[i_b])
            flagged.add(i_b)

        if near_dupes:
            flagged.add(i_a)
            groups.append(
                DuplicateGroup(
                    canonical=files[i_a],
                    duplicates=near_dupes,
                    match_type="near_duplicate",
                    similarity=round(sim, 3),
                )
            )

    return groups
