"""
Phase 2: Technical Presales — Copy + Rename Presentations

Workflow:
  1. Plan  (default): AIClassifier parses all filenames (Sonnet API or regex),
     builds per-client subfolder paths, resolves duplicates, saves plan JSON.
  2. Execute (--execute): reads plan JSON, copies renamed files.
  3. Diff   (--diff):     generates a fresh Sonnet plan and diffs against
     the existing phase2_plan.json to show improvements.

Destination:
  MyWork/00_Tech_PreSales/80_Archive/Presentations_Delivered/
    ClientName/
      PRES_Description_YYYY-MM-DD[_v02].pptx

Usage:
    python scripts/phase2_presales_rename.py              # plan via Sonnet
    python scripts/phase2_presales_rename.py --no-api     # plan via regex
    python scripts/phase2_presales_rename.py --diff       # diff old vs Sonnet
    python scripts/phase2_presales_rename.py --execute    # copy per plan
"""

import argparse
import datetime
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import get_settings
from src.core.llm.classifier import AIClassifier, PlanEntry


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SOURCE_REL    = "Projects/_Technical Presales/Presentations Delivered"
DEST_BASE_REL = "MyWork/00_Tech_PreSales/80_Archive/Presentations_Delivered"

DEFAULT_PLAN = Path(__file__).parent / "phase2_plan.json"

TYPE_MAP = {
    ".pptx": "PRES", ".pptm": "PRES", ".ppt": "PRES",
    ".pdf":  "DOC",  ".docx": "DOC",  ".doc": "DOC",
    ".mp4":  "REC",  ".mkv":  "REC",  ".m4a": "REC",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lp(path: Path) -> str:
    """Windows extended-length path prefix."""
    return "\\\\?\\" + os.path.abspath(str(path))


def mtime_date(path: Path) -> str:
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def sanitize(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text.strip())
    return s.strip("_")[:max_len]


def valid_iso(d: str | None) -> bool:
    return bool(d and re.match(r"^\d{4}-\d{2}-\d{2}$", d))


def make_filename(type_code: str, description: str, date: str, ext: str) -> str:
    return f"{type_code}_{sanitize(description)}_{date}{ext.lower()}"


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------

def build_plan(
    src_dir: Path,
    dest_base: Path,
    provider: str = "auto",
) -> list[dict]:
    """
    Classify all filenames and assemble plan entries.
    Returns list of plain dicts (JSON-serialisable).
    """
    files = sorted(f for f in src_dir.iterdir() if f.is_file())
    print(f"  Found {len(files)} files in source folder")

    clf     = AIClassifier(provider=provider)
    results = clf.classify_filenames([f.name for f in files])

    # Build raw entries (dst filled after duplicate resolution)
    raw = []
    for file, res in zip(files, results):
        date      = res.date if valid_iso(res.date) else None
        date_src  = "filename" if date else "mtime"
        if not date:
            date = mtime_date(file)

        raw.append({
            "original":     file.name,
            "src":          str(file),
            "client":       (res.client or "_Unknown").strip() or "_Unknown",
            "description":  (res.desc or "Technical_Presentation").strip(),
            "date":         date,
            "date_source":  date_src,
            "ambig":        res.ambig,
            "type":         TYPE_MAP.get(file.suffix.lower(), "PRES"),
            "parse_method": res.parse_method,
            "confidence":   res.confidence,
            "status":       "pending",
            "_ext":         file.suffix.lower(),
        })

    _assign_destinations(raw, dest_base)
    return raw


def _assign_destinations(entries: list[dict], dest_base: Path) -> None:
    """
    Set proposed_name and dst on each entry.
    Appends _v02, _v03 … when (client, base_name) would collide.
    """
    seen: dict[str, int] = {}

    for e in entries:
        client   = sanitize(e["client"], max_len=50)
        base     = make_filename(e["type"], e["description"], e["date"], e["_ext"])
        key      = f"{client}/{base}"

        count = seen.get(key, 0) + 1
        seen[key] = count

        if count > 1:
            stem, ext2 = base.rsplit(".", 1)
            base = f"{stem}_v{count:02d}.{ext2}"

        e["proposed_name"] = base
        e["dst"]           = str(dest_base / client / base)

    for e in entries:
        del e["_ext"]


# ---------------------------------------------------------------------------
# Plan display
# ---------------------------------------------------------------------------

def print_plan_summary(plan: list[dict], dest_base: Path) -> None:
    pending    = sum(1 for e in plan if e["status"] == "pending")
    no_date    = sum(1 for e in plan if e["date_source"] == "mtime")
    ambig      = sum(1 for e in plan if e.get("ambig"))
    unknown    = sum(1 for e in plan if e["client"] == "_Unknown")
    clients    = len({e["client"] for e in plan if e["client"] != "_Unknown"})
    dupes      = sum(1 for e in plan if "_v0" in e["proposed_name"])
    low_conf   = sum(1 for e in plan if e.get("confidence") == "low")

    print(f"\n  Files            : {len(plan)}")
    print(f"  Pending copy     : {pending}")
    print(f"  Unique clients   : {clients}")
    print(f"  Client=_Unknown  : {unknown}")
    print(f"  Date from mtime  : {no_date}  (no date in filename)")
    print(f"  Ambiguous dates  : {ambig}  (flag: check before --execute)")
    print(f"  Duplicates       : {dupes}  (_v02 suffix added)")
    print(f"  Low confidence   : {low_conf}")
    print(f"\n  Destination base : {dest_base}")

    print(f"\n{'  Original':<55} Client              Proposed name")
    print(f"  {'-'*54} {'-'*19} {'-'*44}")

    for e in plan[:65]:
        orig   = e["original"][:54]
        client = e["client"][:19]
        prop   = e["proposed_name"][:44]
        flags  = ("!" if e.get("ambig") else "") + ("*" if e["date_source"] == "mtime" else "")
        print(f"  {orig:<55} {client:<20} {prop}{flags}")

    if len(plan) > 65:
        print(f"  ... ({len(plan) - 65} more — see plan JSON)")

    legend = []
    if ambig:   legend.append("! = ambiguous date")
    if no_date: legend.append("* = date from mtime")
    if unknown: legend.append("_Unknown = client not identified")
    if legend:
        print("\n  " + "  |  ".join(legend))


# ---------------------------------------------------------------------------
# Diff: compare two plans
# ---------------------------------------------------------------------------

def diff_plans(old_path: Path, new_path: Path) -> None:
    if not old_path.exists():
        print(f"  Old plan not found: {old_path}")
        return
    if not new_path.exists():
        print(f"  New plan not found: {new_path}")
        return

    old_entries = json.loads(old_path.read_text(encoding="utf-8"))
    new_entries = json.loads(new_path.read_text(encoding="utf-8"))

    old = {e["original"]: e for e in old_entries}
    new = {e["original"]: e for e in new_entries}

    all_files  = sorted(set(old) | set(new))
    agree = differ = 0
    diff_rows: list[tuple] = []

    for fname in all_files:
        oc = (old.get(fname) or {}).get("client", "MISSING")
        nc = (new.get(fname) or {}).get("client", "MISSING")
        if oc == nc:
            agree += 1
        else:
            differ += 1
            diff_rows.append((fname[:52], oc[:22], nc[:22]))

    unk_old = sum(1 for e in old_entries if e["client"] in ("_Unknown", "MISSING"))
    unk_new = sum(1 for e in new_entries if e["client"] in ("_Unknown", "MISSING"))
    old_m   = (next(iter(old.values()), {}) or {}).get("parse_method", "old")
    new_m   = (next(iter(new.values()), {}) or {}).get("parse_method", "new")

    print(f"\n  Files compared      : {len(all_files)}")
    print(f"  Agree on client     : {agree}")
    print(f"  Client differs      : {differ}")
    print(f"  Unknown ({old_m:<7}): {unk_old}")
    print(f"  Unknown ({new_m:<7}): {unk_new}")
    delta = unk_old - unk_new
    print(f"  Delta unknown       : {delta:+d}  ({'new is better' if delta > 0 else 'no improvement' if delta == 0 else 'new is worse'})")

    if diff_rows:
        print(f"\n  {'Filename':<53} {old_m:<23} {new_m}")
        print(f"  {'-'*52} {'-'*22} {'-'*22}")
        for fname, oc, nc in diff_rows[:50]:
            marker = "+" if nc != "_Unknown" and oc == "_Unknown" else (
                     "-" if oc != "_Unknown" and nc == "_Unknown" else " ")
            print(f"  {marker} {fname:<52} {oc:<23} {nc}")
        if len(diff_rows) > 50:
            print(f"  ... ({len(diff_rows) - 50} more differences — check new plan file)")


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    copied:  int = 0
    skipped: int = 0
    cloud:   int = 0
    errors:  list[str] = field(default_factory=list)


def safe_copy(src: Path, dst: Path, stats: Stats) -> str:
    try:
        os.makedirs(lp(dst.parent), exist_ok=True)
        shutil.copy2(lp(src), lp(dst))
        return "ok"
    except OSError as e:
        if getattr(e, "winerror", None) == 389:
            stats.cloud += 1
            return "cloud"
        stats.errors.append(f"[FAIL] {src.name}: {e}")
        return "fail"


def execute_plan(plan: list[dict]) -> Stats:
    stats = Stats()
    for e in plan:
        if e["status"] in ("done", "skip"):
            stats.skipped += 1
            continue

        src = Path(e["src"])
        dst = Path(e["dst"])

        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            e["status"] = "exists"
            stats.skipped += 1
            print(f"  [SKIP]  {e['proposed_name']}")
            continue

        result = safe_copy(src, dst, stats)
        client_dir = Path(e["dst"]).parent.name

        if result == "ok":
            e["status"] = "done"
            stats.copied += 1
            print(f"  [COPY]  {e['original']}")
            print(f"      ->  {client_dir}/{e['proposed_name']}")
        elif result == "cloud":
            e["status"] = "cloud"
            print(f"  [CLOUD] {e['original']}  (not downloaded)")
        else:
            e["status"] = "error"
            print(f"  [FAIL]  {e['original']}  (see WARNINGS)")

    return stats


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    settings  = get_settings()
    onedrive  = settings.onedrive_path
    src_dir   = onedrive / SOURCE_REL
    dest_base = onedrive / DEST_BASE_REL
    plan_file: Path = args.plan_file
    provider  = "regex" if args.no_api else "deepseek"

    # ------------------------------------------------------------------
    # DIFF mode
    # ------------------------------------------------------------------
    if args.diff:
        new_plan_path = plan_file.parent / "phase2_plan_new.json"
        print(f"\n{'='*60}")
        print(f"Phase 2: Diff  [{provider}]")
        print(f"  Old: {plan_file}")
        print(f"  New: {new_plan_path}")
        print(f"{'='*60}")

        if not src_dir.exists():
            print(f"\nERROR: source not found: {src_dir}")
            sys.exit(1)

        print(f"\n--- Building new plan ---\n")
        new_plan = build_plan(src_dir, dest_base, provider=provider)
        new_plan_path.write_text(
            json.dumps(new_plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n  New plan saved to: {new_plan_path}")

        print("\n--- Diff ---")
        diff_plans(plan_file, new_plan_path)

        print(f"\nTo adopt: rename {new_plan_path.name} -> {plan_file.name}")
        return

    # ------------------------------------------------------------------
    # PLAN mode
    # ------------------------------------------------------------------
    if not args.execute:
        print(f"\n{'='*60}")
        print(f"Phase 2: Plan  [{provider}]")
        print(f"  Source    : {src_dir}")
        print(f"  Dest base : {dest_base}")
        print(f"  Plan file : {plan_file}")
        print(f"{'='*60}")

        if not src_dir.exists():
            print(f"\nERROR: source not found: {src_dir}")
            sys.exit(1)

        print(f"\n--- Classifying filenames ---\n")
        plan = build_plan(src_dir, dest_base, provider=provider)

        print("\n--- Plan summary ---")
        print_plan_summary(plan, dest_base)

        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nPlan saved: {plan_file}")
        print("Review / edit (fix ambig!, unknown clients?),")
        print("then run with --execute.")
        return

    # ------------------------------------------------------------------
    # EXECUTE mode
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"Phase 2: Execute")
    print(f"  Plan: {plan_file}")
    print(f"{'='*60}")

    if not plan_file.exists():
        print(f"\nERROR: plan not found: {plan_file}")
        print("Run without --execute first.")
        sys.exit(1)

    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    pending = sum(1 for e in plan if e["status"] not in ("done", "skip", "exists"))
    print(f"\nLoaded {len(plan)} entries, {pending} pending\n")

    stats = execute_plan(plan)
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    if stats.errors:
        print("\n--- WARNINGS ---")
        for w in stats.errors:
            print(f"  {w}")

    print(f"\n{'-'*60}")
    print(f"=== SUMMARY [EXECUTE] ===\n")
    print(f"  Copied   : {stats.copied}")
    print(f"  Skipped  : {stats.skipped}")
    print(f"  Cloud    : {stats.cloud}")
    print(f"  Errors   : {len(stats.errors)}")
    print(f"\n{'='*60}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 2: Copy + rename presentations with AI classification."
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Copy files per plan JSON",
    )
    parser.add_argument(
        "--no-api", action="store_true",
        help="Use regex parser instead of Sonnet API",
    )
    parser.add_argument(
        "--diff", action="store_true",
        help="Generate fresh Sonnet plan and diff against existing plan",
    )
    parser.add_argument(
        "--plan-file", type=Path, default=DEFAULT_PLAN,
        metavar="PATH",
        help=f"Plan JSON path (default: {DEFAULT_PLAN})",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
