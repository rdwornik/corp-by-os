"""
Phase 2: Technical Presales — Copy + Rename Presentations

Two-stage workflow:
  1. Plan  (default): parses all 260 filenames (via Sonnet API or regex fallback),
     writes plan JSON so you can review before touching any files.
  2. Execute (--execute): reads the plan JSON and copies files with renamed targets.

Usage:
    python scripts/phase2_presales_rename.py                     # create plan (uses Sonnet)
    python scripts/phase2_presales_rename.py --no-api            # create plan (regex only, no API)
    python scripts/phase2_presales_rename.py --execute           # copy per plan
    python scripts/phase2_presales_rename.py --plan-file my.json # custom plan path
    python scripts/phase2_presales_rename.py --plan-file my.json --execute

Reads OneDrive path from CORP_ONEDRIVE_PATH env var (or .env).
Plan is saved to scripts/phase2_plan.json by default.
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


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SOURCE_REL = "Projects/_Technical Presales/Presentations Delivered"
DEST_REL   = "MyWork/00_Tech_PreSales/80_Archive/Presentations_Delivered"

DEFAULT_PLAN = Path(__file__).parent / "phase2_plan.json"

BATCH_SIZE = 40  # filenames per Sonnet call

TYPE_MAP = {
    ".pptx": "PRES",
    ".pptm": "PRES",
    ".ppt":  "PRES",
    ".pdf":  "DOC",
    ".docx": "DOC",
    ".mp4":  "REC",
    ".mkv":  "REC",
    ".m4a":  "REC",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def lp(path: Path) -> str:
    """Windows extended-length path string — bypasses 260-char MAX_PATH."""
    return "\\\\?\\" + os.path.abspath(str(path))


def mtime_date(path: Path) -> str:
    """Return ISO date string from file modification time."""
    ts = path.stat().st_mtime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def sanitize(text: str) -> str:
    """
    Convert arbitrary text to safe filename component.
    Keeps letters, digits, underscores. Collapses runs.
    """
    import re
    text = text.strip()
    text = re.sub(r"[^A-Za-z0-9_]", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("_")
    return text


def build_proposed_name(entry: dict, src_path: Path) -> str:
    """Compose PRES_Client_Description_YYYY-MM-DD.ext from a plan entry."""
    ext   = Path(entry["original"]).suffix.lower()
    code  = TYPE_MAP.get(ext, "PRES")
    client = sanitize(entry.get("client") or "Unknown")
    desc   = sanitize(entry.get("description") or "Presentation")
    date   = entry.get("date") or mtime_date(src_path)

    # Basic validation: date must look like YYYY-MM-DD
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = mtime_date(src_path)

    return f"{code}_{client}_{desc}_{date}{ext}"


# ---------------------------------------------------------------------------
# Sonnet parsing
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a filename parser. Extract structured metadata from presentation filenames.
Output only valid JSON — no markdown, no explanation."""

PARSE_INSTRUCTIONS = """\
Parse these presentation filenames. For each file, return JSON with:
  - "original": exact original filename
  - "client": company being presented to (PascalCase, underscores, max 3 words)
  - "description": session type/topic (PascalCase, underscores, 2-4 words)
  - "date": ISO date YYYY-MM-DD extracted from filename, or null

Rules:
- client = the CUSTOMER company, not Blue Yonder or BY
- Strip leading "LOCAL " prefix (means EMEA delivery) — client is still the customer
- "Mike's slides for Pfizer" → client=Pfizer
- "BY presentation Bel" → client=Bel
- "BY SaaS ACEHardware" → client=ACE_Hardware
- "MIKES SLIDES ... Presentation to Yonderland" → client=Yonderland
- If no customer identifiable (internal/generic) → client=Internal
- description: pick best label from: Technical_Overview, RFP_Presentation,
  Discovery_Workshop, Integration_Workshop, Technology_Session, Architecture_Review,
  Platform_Demo, SaaS_Overview, Demo, Onboarding, or derive a short label
- date patterns (all return YYYY-MM-DD):
    "2022-09-14" or "2022 09 13" → 2022-09-14 / 2022-09-13
    "20220810" → 2022-08-10
    "22-06-27" or "22.06.27" → 2022-06-27
    "24_03_22" → 2024-03-22
    "24.01.22" or "11.02.2022" → 2022-01-24 / 2022-11-02  (DD.MM.YY / DD.MM.YYYY)
    "06-29-2023" → 2023-06-29 (MM-DD-YYYY)
    "April 2023" → 2023-04-01
    "2023-11" → 2023-11-01
    "20230209" → 2023-02-09
    If no date found → null
- Do NOT guess dates that are not explicitly in the filename

Return a JSON array, one object per filename, same order as input.

Filenames:
"""


# ---------------------------------------------------------------------------
# Regex fallback parser
# ---------------------------------------------------------------------------

# Token-level noise words (matched case-insensitively against individual tokens)
_NOISE_TOKENS = {
    "blue", "yonder", "by", "saas", "technology", "technical", "platform",
    "integration", "architecture", "presentation", "overview", "session",
    "workshop", "rfp", "rfi", "rft", "demo", "discussion", "review",
    "summary", "slides", "slide", "deck", "deepdive", "deep", "dive",
    "followup", "follow", "solution", "discovery", "mikes",
    "local", "geller", "mike", "v1", "v2", "v3", "v4", "v5",
    "final", "copy", "updated", "template", "draft", "new", "old",
    "for", "and", "the", "with", "from", "to", "in", "of", "at",
    "lp", "ep", "ms", "emea", "amer", "apac",
}

# Month name → number
_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09",
    "oct": "10", "nov": "11", "dec": "12",
}


def _extract_date_regex(stem: str) -> tuple[str | None, str]:
    """
    Try to find a date in stem. Returns (iso_date_or_None, stem_with_date_removed).
    Tries patterns in order of specificity.
    """
    s = stem

    # 1. YYYY-MM-DD or YYYY MM DD or YYYY_MM_DD (4-digit year first)
    m = re.search(r'(\d{4})[\s._-](\d{2})[\s._-](\d{2})', s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        if 2019 <= int(y) <= 2026 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}", s[:m.start()] + " " + s[m.end():]

    # 2. YYYYMMDD (compact, 4-digit year)
    m = re.search(r'(\d{4})(\d{2})(\d{2})', s)
    if m:
        y, mo, d = m.group(1), m.group(2), m.group(3)
        if 2019 <= int(y) <= 2026 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}", s[:m.start()] + " " + s[m.end():]

    # 3. YY-MM-DD or YY.MM.DD or YY_MM_DD (2-digit year at start, e.g. 22-06-27, 24_03_22)
    #    Use (?<!\d)/(?!\d) instead of \b to avoid underscore boundary issues
    m = re.search(r'(?<!\d)(\d{2})[_\-.](\d{2})[_\-.](\d{2})(?!\d)', s)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        # Distinguish DD.MM.YY from YY.MM.DD:
        if int(a) > 31:  # definitely a year prefix (e.g. "22" as 2022)
            return f"20{a}-{b}-{c}", s[:m.start()] + " " + s[m.end():]
        if int(c) > 31:  # c must be year suffix (DD.MM.YY European)
            return f"20{c}-{b}-{a}", s[:m.start()] + " " + s[m.end():]
        if int(b) > 12:  # b can't be month → YY-DD-MM unlikely; treat as YY-MM-DD
            return f"20{a}-{b}-{c}", s[:m.start()] + " " + s[m.end():]
        # Default: assume YY-MM-DD (year first — most common in this dataset)
        return f"20{a}-{b}-{c}", s[:m.start()] + " " + s[m.end():]

    # 4. DD.MM.YYYY or MM-DD-YYYY (4-digit year, dot or dash separated)
    m = re.search(r'(\d{2})[.\-](\d{2})[.\-](\d{4})', s)
    if m:
        a, b, y = m.group(1), m.group(2), m.group(3)
        if 2019 <= int(y) <= 2026:
            if int(a) > 12:
                # a can't be month → DD.MM.YYYY (European)
                return f"{y}-{b}-{a}", s[:m.start()] + " " + s[m.end():]
            if int(b) > 12:
                # b can't be month → MM.DD.YYYY (American)
                return f"{y}-{a}-{b}", s[:m.start()] + " " + s[m.end():]
            # Ambiguous: default to DD.MM.YYYY (European — most common in this dataset)
            if 1 <= int(a) <= 31 and 1 <= int(b) <= 12:
                return f"{y}-{b}-{a}", s[:m.start()] + " " + s[m.end():]

    # 5. DDMonYY or DDMonYYYY (e.g. "05Apr22", "20Oct22")
    month_pat = '|'.join(_MONTHS.keys())
    m = re.search(r'(\d{1,2})(' + month_pat + r')(\d{2,4})', s, re.IGNORECASE)
    if m:
        d, mon, yr = m.group(1), m.group(2), m.group(3)
        mo = _MONTHS[mon.lower()[:3]]
        y = f"20{yr}" if len(yr) == 2 else yr
        if 2019 <= int(y) <= 2026 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d.zfill(2)}", s[:m.start()] + " " + s[m.end():]

    # 6. Month name YYYY (e.g. "April 2023", "June 20th 2022")
    m = re.search(
        r'\b(' + month_pat + r')\w*\s+(?:\d{1,2}\w*\s+)?(\d{4})\b',
        s, re.IGNORECASE,
    )
    if m:
        mo = _MONTHS[m.group(1).lower()[:3]]
        y = m.group(2)
        return f"{y}-{mo}-01", s[:m.start()] + " " + s[m.end():]

    # 7. YYYY-MM only (e.g. "2023-11")
    m = re.search(r'\b(\d{4})-(\d{2})\b', s)
    if m:
        y, mo = m.group(1), m.group(2)
        if 2019 <= int(y) <= 2026 and 1 <= int(mo) <= 12:
            return f"{y}-{mo}-01", s[:m.start()] + " " + s[m.end():]

    return None, s


def _tokenize(s: str) -> list[str]:
    """Split on any non-alphanumeric-ampersand character into tokens."""
    return [t for t in re.split(r'[^A-Za-z0-9&]+', s) if t]


def _is_noise(token: str) -> bool:
    return token.lower() in _NOISE_TOKENS or len(token) <= 1 or token.isdigit()


def _extract_client_regex(stem: str) -> str:
    """
    Token-based client extraction.
    Splits on underscores/spaces/dashes, filters noise, takes first meaningful tokens.
    """
    s = stem.strip()

    # Strip leading "LOCAL " or "LOCAL_"
    s = re.sub(r'^local[\s_]+', '', s, flags=re.IGNORECASE)

    # "Mike's slides for <Client>" → explicit rule
    m = re.search(
        r"mike'?s\s+slides?\s+for\s+([A-Za-z][A-Za-z0-9&\s]+?)(?:\s*\d|\s*$)",
        s, re.IGNORECASE,
    )
    if m:
        return _to_pascal(m.group(1).strip())

    # "Presentation to <Client>"
    m = re.search(r'presentation\s+to\s+([A-Za-z][A-Za-z0-9&\s]+?)(?:\s|$)', s, re.IGNORECASE)
    if m:
        return _to_pascal(m.group(1).strip())

    # "BY presentation <Client>" or "BY SaaS <Client>"
    m = re.search(r'\bby\s+(?:presentation|saas|wms|tms|platform)\s+([A-Za-z]\w+)', s, re.IGNORECASE)
    if m:
        return m.group(1).capitalize()

    # General: tokenize, filter noise, take first 1-2 meaningful tokens
    tokens = _tokenize(s)
    client_tokens = []
    for tok in tokens:
        if _is_noise(tok):
            continue
        if not re.match(r'^[A-Za-z0-9&]+$', tok):
            continue
        client_tokens.append(tok)
        if len(client_tokens) == 2:
            break

    if not client_tokens:
        return "Unknown"

    return "_".join(t.capitalize() for t in client_tokens)


def _extract_description_regex(stem: str) -> str:
    """Classify session type from keywords in the filename stem."""
    s = stem.lower()

    if re.search(r'\brfp\b', s):
        return "RFP_Presentation"
    if re.search(r'\brfi\b|\brft\b', s):
        return "RFI_Presentation"
    if re.search(r'\bworkshop\b', s):
        return "Workshop"
    if re.search(r'\bdiscovery\b', s):
        return "Discovery_Session"
    if re.search(r'\bintegration\b', s):
        return "Integration_Overview"
    if re.search(r'\barchitecture\b', s):
        return "Architecture_Review"
    if re.search(r'\bdemo\b|\bdemonstration\b', s):
        return "Demo"
    if re.search(r'\bdeep.?dive\b', s):
        return "Deep_Dive"
    if re.search(r'\bfollow.?up\b', s):
        return "Follow_Up"
    if re.search(r'\bonboard\b', s):
        return "Onboarding"
    if re.search(r'\btraining\b', s):
        return "Training"
    return "Technical_Presentation"


def _to_pascal(text: str) -> str:
    """Convert 'some company name' to 'Some_Company_Name'."""
    parts = re.split(r'[\s_-]+', text.strip())
    return "_".join(p.capitalize() for p in parts if p)


def parse_filename_regex(name: str) -> dict:
    """
    Pure-regex parsing. Extracts client, description, date.
    Marks client='Unknown' when uncertain.
    """
    stem = Path(name).stem
    date, stem_clean = _extract_date_regex(stem)
    client      = _extract_client_regex(stem_clean)
    description = _extract_description_regex(stem)

    return {
        "original":    name,
        "client":      client,
        "description": description,
        "date":        date,
    }


# ---------------------------------------------------------------------------
# Sonnet batch parser
# ---------------------------------------------------------------------------

def parse_batch(filenames: list[str], llm) -> list[dict]:
    """Call Sonnet for one batch of filenames. Returns list of dicts."""
    from src.core.llm.sonnet import get_client as _get_client  # local import to allow --no-api

    names_block = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(filenames))
    prompt = PARSE_INSTRUCTIONS + names_block

    try:
        result = llm.complete_json(prompt, system=SYSTEM_PROMPT, max_tokens=4096)
    except Exception as e:
        print(f"  [WARN] Sonnet error for batch: {type(e).__name__}: {e}")
        return [parse_filename_regex(n) for n in filenames]

    # Normalise: result may be a list or a dict with a "files" key
    if isinstance(result, dict):
        result = list(result.values())[0]
    if not isinstance(result, list):
        result = []

    # Patch missing entries
    while len(result) < len(filenames):
        result.append(parse_filename_regex(filenames[len(result)]))

    return result[:len(filenames)]


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------

def build_plan(src_dir: Path, dst_dir: Path, use_api: bool = True) -> list[dict]:
    """
    Scan src_dir, parse filenames (via Sonnet or regex), return plan list.
    Each plan entry has: original, src, proposed_name, dst, client,
    description, date, date_source, parse_method, status.
    """
    files = sorted(f for f in src_dir.iterdir() if f.is_file())
    print(f"  Found {len(files)} files in source folder")

    filenames = [f.name for f in files]
    parsed: list[dict] = []

    if use_api:
        from src.core.llm.sonnet import get_client
        llm = get_client()
        batches = [filenames[i:i+BATCH_SIZE] for i in range(0, len(filenames), BATCH_SIZE)]
        for batch_num, batch in enumerate(batches, 1):
            print(f"  Calling Sonnet: batch {batch_num}/{len(batches)} ({len(batch)} files)...")
            result = parse_batch(batch, llm)
            parsed.extend(result)
        parse_method = "sonnet"
    else:
        print(f"  Using regex parser (--no-api mode)...")
        parsed = [parse_filename_regex(n) for n in filenames]
        parse_method = "regex"

    plan = []
    for file, meta in zip(files, parsed):
        date_source = "filename" if meta.get("date") else "mtime"
        proposed    = build_proposed_name(meta, file)
        entry = {
            "original":      file.name,
            "src":           str(file),
            "proposed_name": proposed,
            "dst":           str(dst_dir / proposed),
            "client":        meta.get("client") or "Unknown",
            "description":   meta.get("description") or "Presentation",
            "date":          meta.get("date") or mtime_date(file),
            "date_source":   date_source,
            "parse_method":  parse_method,
            "status":        "pending",
        }
        plan.append(entry)

    return plan


# ---------------------------------------------------------------------------
# Plan display
# ---------------------------------------------------------------------------

def print_plan(plan: list[dict], dst_dir: Path) -> None:
    """Print a readable summary of the plan."""
    pending  = [e for e in plan if e["status"] == "pending"]
    done     = [e for e in plan if e["status"] == "done"]
    skipped  = [e for e in plan if e["status"] in ("skip", "exists")]
    no_date  = [e for e in plan if e.get("date_source") == "mtime"]
    unknown  = [e for e in plan if e.get("client") == "Unknown" or e.get("client") == "Internal"]

    print(f"\n  Total entries   : {len(plan)}")
    print(f"  Pending copy    : {len(pending)}")
    print(f"  Already done    : {len(done)}")
    print(f"  Date from mtime : {len(no_date)}  (no date in filename)")
    print(f"  Client=Unknown  : {len(unknown)}")
    print(f"\n  Destination: {dst_dir}")

    print(f"\n{'  original':<55} {'proposed name'}")
    print(f"  {'-'*54} {'-'*54}")
    for e in plan[:50]:  # cap preview at 50 lines
        orig  = e["original"][:54]
        prop  = e["proposed_name"][:54]
        mark  = " *" if e.get("date_source") == "mtime" else ""
        print(f"  {orig:<55} {prop}{mark}")
    if len(plan) > 50:
        print(f"  ... ({len(plan) - 50} more entries — see plan JSON)")

    if no_date:
        print(f"\n  (* = date taken from file mtime, not filename)")


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    copied: int = 0
    skipped: int = 0
    cloud: int = 0
    errors: list[str] = field(default_factory=list)


def safe_copy(src: Path, dst: Path, stats: Stats) -> str:
    """Copy with long-path support. Returns 'ok', 'cloud', or 'fail'."""
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
    """Copy files per plan. Updates plan entry status in-place."""
    stats = Stats()

    for entry in plan:
        if entry["status"] in ("done", "skip"):
            stats.skipped += 1
            continue

        src = Path(entry["src"])
        dst = Path(entry["dst"])

        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            entry["status"] = "exists"
            stats.skipped += 1
            print(f"  [SKIP]  {entry['proposed_name']}")
            continue

        result = safe_copy(src, dst, stats)
        if result == "ok":
            entry["status"] = "done"
            stats.copied += 1
            print(f"  [COPY]  {entry['original']}")
            print(f"      ->  {entry['proposed_name']}")
        elif result == "cloud":
            entry["status"] = "cloud"
            print(f"  [CLOUD] {entry['original']}  (not downloaded)")
        else:
            entry["status"] = "error"
            print(f"  [FAIL]  {entry['original']}  (see WARNINGS)")

    return stats


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(dry_run: bool, plan_file: Path, use_api: bool = True) -> None:
    settings = get_settings()
    onedrive = settings.onedrive_path
    src_dir  = onedrive / SOURCE_REL
    dst_dir  = onedrive / DEST_REL

    mode = "PLAN" if dry_run else "EXECUTE"
    parser_label = "Sonnet" if use_api else "regex"
    print(f"\n{'='*60}")
    print(f"Phase 2: Presales Rename  [{mode}]  (parser: {parser_label})")
    print(f"Source : {src_dir}")
    print(f"Dest   : {dst_dir}")
    print(f"Plan   : {plan_file}")
    print(f"{'='*60}")

    if not src_dir.exists():
        print(f"\nERROR: source not found: {src_dir}")
        print("Set CORP_ONEDRIVE_PATH in your .env file.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # PLAN mode
    # ------------------------------------------------------------------
    if dry_run:
        label = "calling Sonnet" if use_api else "regex only, no API"
        print(f"\n--- Building plan ({label}) ---\n")
        plan = build_plan(src_dir, dst_dir, use_api=use_api)

        print("\n--- Plan preview ---")
        print_plan(plan, dst_dir)

        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nPlan saved to: {plan_file}")
        print("\nReview the plan JSON, edit client/description/date if needed,")
        print("then run with --execute to copy files.")
        return

    # ------------------------------------------------------------------
    # EXECUTE mode
    # ------------------------------------------------------------------
    if not plan_file.exists():
        print(f"\nERROR: plan file not found: {plan_file}")
        print("Run without --execute first to create the plan.")
        sys.exit(1)

    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    print(f"\nLoaded plan: {len(plan)} entries")

    pending = sum(1 for e in plan if e["status"] not in ("done", "skip", "exists"))
    print(f"Pending    : {pending}")

    dst_dir.mkdir(parents=True, exist_ok=True)

    print("\n--- Copying files ---\n")
    stats = execute_plan(plan)

    # Save updated plan (with status changes)
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    if stats.errors:
        print(f"\n--- WARNINGS ---")
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
        description="Phase 2: Copy + rename presentations into MyWork structure."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually copy files (default is plan/dry-run mode)",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Use regex parser only, skip Sonnet API calls",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=DEFAULT_PLAN,
        help=f"Path to plan JSON file (default: {DEFAULT_PLAN})",
    )
    args = parser.parse_args()
    run(dry_run=not args.execute, plan_file=args.plan_file, use_api=not args.no_api)


if __name__ == "__main__":
    main()
