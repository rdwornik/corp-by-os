"""MyWork full audit — read-only scan + Gemini analysis + vault coverage.

Scans every file in MyWork (except 80_Archive), sends per-folder file listings
to Gemini for analysis, cross-references with vault extraction coverage, and
produces a structured JSON report for downstream planning.

This module is READ ONLY — it never moves, renames, or deletes files.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Folders to skip entirely during scan
SKIP_FOLDERS: set[str] = {"80_Archive", ".corp", "__pycache__", ".git", ".venv", "node_modules"}

# L1 folders that get individual Gemini analysis
ANALYSIS_FOLDERS: list[str] = [
    "00_Inbox",
    "10_Projects",
    "20_Extra_Initiatives",
    "30_Templates",
    "40_Assets_Recordings",
    "50_RFP",
    "60_Source_Library",
    "70_Admin",
    "90_System",
]

_FOLDER_ANALYSIS_PROMPT = """\
You are auditing a pre-sales engineer's work folder.
This engineer works at Blue Yonder, doing enterprise supply chain software pre-sales across EMEA.

Here is the complete file listing for folder: {folder_name}
{file_listing}

Analyze this folder and produce a JSON response with these keys:

1. "summary": (string, 2-3 sentences) What is this folder for? What does it contain?

2. "structure_score": (string, one of "good", "needs_work", "chaotic")
   Is the folder well-organized? Are naming conventions consistent? Is the hierarchy logical?

3. "valuable": (array of objects) Most important/useful files:
   Each: {{"file": "filename", "reason": "why valuable"}}

4. "junk": (array of strings) Files that could be deleted or archived:
   Empty files, duplicates, temp files, .url bookmarks, old versions, conflict copies.

5. "misplaced": (array of objects) Files in wrong folder:
   Each: {{"file": "filename", "should_be": "target_folder", "reason": "why"}}

6. "media": (array of objects, if any video/audio files exist) Media assessment:
   Each: {{"file": "filename", "size_mb": N, "worth_extracting": bool, "reason": "why or why not"}}

7. "action_items": (array of objects) Concrete next steps ordered by impact:
   Each: {{"action": "description", "effort": "quick|medium|big", "files": ["file1", ...]}}

Respond ONLY with a JSON object. No markdown fences. Be specific — reference actual filenames.
"""

_PROJECT_ANALYSIS_PROMPT = """\
You are auditing project folders for a pre-sales engineer \
at Blue Yonder (EMEA supply chain pre-sales).

Here is a summary of all project folders:
{project_listing}

Produce a CONCISE JSON response. Keep "notes" to max 15 words per project. Keys:

1. "summary": (string, 2 sentences max)

2. "projects": (array) Per-project:
   {{"name": "str", "status_guess": "active|dormant|completed|unclear", \
"structure": "good|needs_work|chaotic", "notes": "15 words max"}}

3. "action_items": (array, max 5)
   {{"action": "str", "effort": "quick|medium|big", "projects": ["proj1"]}}

Respond ONLY with valid JSON. No markdown. No code fences. Be brief.
"""


# --- Step 1: Filesystem scan ---


def scan_mywork(mywork_root: Path) -> list[dict[str, Any]]:
    """Scan all files in MyWork, returning metadata for each.

    Skips 80_Archive and hidden/system folders.
    """
    all_files: list[dict[str, Any]] = []

    for f in mywork_root.rglob("*"):
        if not f.is_file():
            continue

        # Skip files inside excluded folders
        try:
            rel = f.relative_to(mywork_root)
        except ValueError:
            continue

        parts = rel.parts
        if any(skip in parts for skip in SKIP_FOLDERS):
            continue

        try:
            stat = f.stat()
            all_files.append(
                {
                    "path": str(rel).replace("\\", "/"),
                    "name": f.name,
                    "ext": f.suffix.lower(),
                    "size_bytes": stat.st_size,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(
                        timespec="seconds",
                    ),
                    "folder_l1": parts[0] if parts else "",
                    "folder_l2": parts[1] if len(parts) > 1 else "",
                    "folder_l3": parts[2] if len(parts) > 2 else "",
                }
            )
        except OSError:
            logger.debug("Cannot stat %s, skipping", f)

    logger.info("Scanned %d files in %s", len(all_files), mywork_root)
    return all_files


# --- Step 2: Gemini analysis ---


def _get_gemini_client() -> Any:
    """Create a Gemini client from GEMINI_API_KEY."""
    from google import genai  # type: ignore[import-untyped]

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # Try loading from CKE .env
        try:
            from corp_by_os.overnight.cke_client import _ensure_cke_importable

            _ensure_cke_importable()
            api_key = os.environ.get("GEMINI_API_KEY")
        except Exception:
            pass

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set. Set it in .env or load CKE's .env.")

    return genai.Client(api_key=api_key)


def _parse_gemini_json(text: str) -> dict[str, Any]:
    """Parse Gemini response, stripping markdown fences if present.

    If JSON is truncated (common with large responses), attempts to
    repair by closing open structures.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        cleaned = "\n".join(lines[1:end]).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Attempt repair: close truncated JSON
        repaired = _repair_truncated_json(cleaned)
        return json.loads(repaired)


def _repair_truncated_json(text: str) -> str:
    """Best-effort repair of truncated JSON from Gemini.

    Handles common truncation patterns: unclosed strings, arrays, objects.
    """
    # Trim to last valid-looking line
    lines = text.rstrip().split("\n")
    while lines and not lines[-1].strip():
        lines.pop()

    # If last line is a truncated string value, close it
    joined = "\n".join(lines)

    # Count open/close brackets
    in_string = False
    escape = False
    opens: list[str] = []
    i = 0
    while i < len(joined):
        ch = joined[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if ch == '"' and not escape:
            in_string = not in_string
        elif not in_string:
            if ch in ("{", "["):
                opens.append(ch)
            elif ch == "}" and opens and opens[-1] == "{":
                opens.pop()
            elif ch == "]" and opens and opens[-1] == "[":
                opens.pop()
        i += 1

    # If we're inside a string, close it
    if in_string:
        joined += '"'

    # Remove trailing commas before closing brackets
    import re as _re

    joined = _re.sub(r",\s*$", "", joined)

    # Close any open structures
    for bracket in reversed(opens):
        if bracket == "{":
            joined += "}"
        elif bracket == "[":
            joined += "]"

    # Clean up trailing commas before closing brackets (from repair)
    joined = _re.sub(r",(\s*[}\]])", r"\1", joined)

    return joined


def _build_file_listing(files: list[dict[str, Any]]) -> str:
    """Build a compact file listing string for the prompt."""
    lines = []
    for f in sorted(files, key=lambda x: x["path"]):
        lines.append(f"  {f['path']}  ({f['size_mb']} MB, modified {f['modified'][:10]})")
    return "\n".join(lines)


def _build_project_listing(files: list[dict[str, Any]]) -> str:
    """Build a project summary for 10_Projects (folder names + counts + sizes)."""
    projects: dict[str, dict[str, Any]] = {}
    for f in files:
        proj = f["folder_l2"]
        if not proj:
            continue
        if proj not in projects:
            projects[proj] = {"file_count": 0, "size_mb": 0.0, "extensions": Counter()}
        projects[proj]["file_count"] += 1
        projects[proj]["size_mb"] += f["size_mb"]
        if f["ext"]:
            projects[proj]["extensions"][f["ext"]] += 1

    lines = []
    for name, info in sorted(projects.items()):
        top_ext = ", ".join(f"{ext}({n})" for ext, n in info["extensions"].most_common(5))
        lines.append(
            f"  {name}: {info['file_count']} files, {info['size_mb']:.1f} MB, types: {top_ext}"
        )
    return "\n".join(lines)


def analyze_folder(
    folder_name: str,
    files: list[dict[str, Any]],
    client: Any,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Send folder file listing to Gemini for analysis.

    For 10_Projects, sends project summaries instead of individual files.
    """
    from google import genai as genai_module  # type: ignore[import-untyped]

    if folder_name == "10_Projects":
        listing = _build_project_listing(files)
        prompt = _PROJECT_ANALYSIS_PROMPT.format(project_listing=listing)
    else:
        listing = _build_file_listing(files)
        prompt = _FOLDER_ANALYSIS_PROMPT.format(
            folder_name=folder_name,
            file_listing=listing,
        )

    logger.info("Analyzing %s (%d files)...", folder_name, len(files))

    # 10_Projects needs more tokens (33 project analyses)
    max_tokens = 16384 if folder_name == "10_Projects" else 8192

    raw_text = None
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_module.types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=max_tokens,
            ),
        )
        raw_text = response.text
        parsed = _parse_gemini_json(raw_text)
        return {
            "folder": folder_name,
            "file_count": len(files),
            "analysis": parsed,
            "raw_response": raw_text,
            "model": model,
            "error": None,
        }
    except Exception as exc:
        logger.warning("Analysis failed for %s: %s", folder_name, exc)
        return {
            "folder": folder_name,
            "file_count": len(files),
            "analysis": None,
            "raw_response": raw_text,  # preserve raw even on parse failure
            "model": model,
            "error": str(exc),
        }


def analyze_all_folders(
    all_files: list[dict[str, Any]],
    model: str = "gemini-2.5-flash",
) -> list[dict[str, Any]]:
    """Run Gemini analysis on each L1 folder."""
    client = _get_gemini_client()

    # Group files by L1 folder
    by_folder: dict[str, list[dict[str, Any]]] = {}
    for f in all_files:
        l1 = f["folder_l1"]
        if l1 not in by_folder:
            by_folder[l1] = []
        by_folder[l1].append(f)

    results = []
    for folder_name in ANALYSIS_FOLDERS:
        files = by_folder.get(folder_name, [])
        if not files:
            logger.info("Skipping %s (no files)", folder_name)
            continue

        result = analyze_folder(folder_name, files, client, model)
        results.append(result)

    return results


# --- Step 3: Vault coverage ---


def check_vault_coverage(
    all_files: list[dict[str, Any]],
    vault_path: Path,
) -> dict[str, Any]:
    """Cross-reference MyWork files with vault notes.

    Checks which files have corresponding extracted notes in
    02_sources or 04_evergreen.
    """
    # Collect vault note stems for fuzzy matching
    vault_stems: set[str] = set()
    for zone in ("02_sources", "04_evergreen"):
        zone_dir = vault_path / zone
        if not zone_dir.exists():
            continue
        for md in zone_dir.rglob("*.md"):
            # Normalize: lowercase, replace separators
            stem = md.stem.lower().replace(" ", "-").replace("_", "-")
            vault_stems.add(stem)

    logger.info("Found %d vault note stems for coverage check", len(vault_stems))

    extracted = []
    not_extracted = []
    by_folder: dict[str, dict[str, int]] = {}

    for f in all_files:
        # Normalize file stem for matching
        stem = Path(f["name"]).stem.lower().replace(" ", "-").replace("_", "-")
        # Fuzzy: check if first 20 chars of stem appear in any vault note
        has_note = any(stem[:20] in vs for vs in vault_stems) if len(stem) >= 5 else False

        if has_note:
            extracted.append(f["path"])
        else:
            not_extracted.append(f["path"])

        l1 = f["folder_l1"]
        if l1 not in by_folder:
            by_folder[l1] = {"extracted": 0, "not_extracted": 0}
        if has_note:
            by_folder[l1]["extracted"] += 1
        else:
            by_folder[l1]["not_extracted"] += 1

    return {
        "total_vault_notes": len(vault_stems),
        "extracted_count": len(extracted),
        "not_extracted_count": len(not_extracted),
        "by_folder": by_folder,
        "top_unextracted_folders": Counter(
            f["folder_l1"] for f in all_files if f["path"] in set(not_extracted)
        ).most_common(10),
    }


# --- Step 4: Build structured report ---


def build_report(
    all_files: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the final structured JSON report."""
    total_size = sum(f["size_bytes"] for f in all_files)

    # Per-folder stats
    folder_stats: dict[str, dict[str, Any]] = {}
    for f in all_files:
        l1 = f["folder_l1"]
        if l1 not in folder_stats:
            folder_stats[l1] = {"file_count": 0, "size_bytes": 0, "extensions": Counter()}
        folder_stats[l1]["file_count"] += 1
        folder_stats[l1]["size_bytes"] += f["size_bytes"]
        if f["ext"]:
            folder_stats[l1]["extensions"][f["ext"]] += 1

    # Build folders section
    folders: dict[str, Any] = {}
    for folder_name, stats in sorted(folder_stats.items()):
        size_mb = round(stats["size_bytes"] / (1024 * 1024), 1)
        entry: dict[str, Any] = {
            "file_count": stats["file_count"],
            "size_mb": size_mb,
            "top_extensions": dict(stats["extensions"].most_common(8)),
        }

        # Merge Gemini analysis if available
        for a in analyses:
            if a["folder"] == folder_name and a["analysis"]:
                entry["gemini_analysis"] = a["analysis"]
                break

        # Add coverage data
        cov = coverage.get("by_folder", {}).get(folder_name, {})
        if cov:
            entry["vault_coverage"] = cov

        folders[folder_name] = entry

    # Media inventory
    media_exts = {".mp4", ".mkv", ".avi", ".mov", ".mp3", ".wav", ".m4a", ".webm"}
    media_files = [
        {
            "path": f["path"],
            "ext": f["ext"],
            "size_mb": f["size_mb"],
            "modified": f["modified"],
        }
        for f in all_files
        if f["ext"] in media_exts
    ]
    media_files.sort(key=lambda x: x["size_mb"], reverse=True)

    # Duplicate candidates (same filename in different locations)
    name_counts = Counter(f["name"] for f in all_files)
    duplicate_candidates = []
    for name, count in name_counts.most_common(50):
        if count < 2:
            break
        paths = [f["path"] for f in all_files if f["name"] == name]
        duplicate_candidates.append({"name": name, "count": count, "paths": paths})

    # Collect recommendations from all analyses
    recommendations = []
    for a in analyses:
        if a["analysis"] and "action_items" in a["analysis"]:
            for item in a["analysis"]["action_items"]:
                item["folder"] = a["folder"]
                recommendations.append(item)

    return {
        "scan_date": datetime.now().isoformat(timespec="seconds"),
        "total_files": len(all_files),
        "total_size_gb": round(total_size / (1024**3), 2),
        "vault_coverage": {
            "total_vault_notes": coverage["total_vault_notes"],
            "extracted": coverage["extracted_count"],
            "not_extracted": coverage["not_extracted_count"],
            "by_folder": coverage.get("by_folder", {}),
        },
        "folders": folders,
        "media_inventory": media_files,
        "duplicate_candidates": duplicate_candidates,
        "recommendations": recommendations,
    }
