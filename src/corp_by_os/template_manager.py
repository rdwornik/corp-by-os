"""Template registry management.

Scans 30_Templates/, maintains registry in 90_System/template_registry.yaml.
Selects best template for a given goal using keyword matching.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import yaml

from corp_by_os.config import get_config
from corp_by_os.models import TemplateInfo

logger = logging.getLogger(__name__)

# File extensions considered templates
_TEMPLATE_EXTENSIONS = {".pptx", ".xlsx", ".docx"}

# Skip directories that don't contain usable templates
_SKIP_DIRS = {"Deprecated", "Working", "Canonical"}

# Type inference by extension
_TYPE_BY_EXT = {
    ".pptx": "presentation",
    ".xlsx": "questionnaire",
    ".docx": "document",
}

# Auto-tag mappings: if filename contains key, add tags + use_cases
_AUTO_TAGS: list[tuple[str, list[str], list[str]]] = [
    # (keyword in filename, tags, use_cases)
    (
        "corporate",
        ["corporate", "overview"],
        ["corporate overview", "executive briefing", "first meeting"],
    ),
    (
        "discovery",
        ["discovery", "questions"],
        ["discovery call preparation", "qualification"],
    ),
    ("customer", ["discovery"], ["discovery call preparation"]),
    ("pitch", ["pitch", "overview"], ["first meeting", "executive briefing"]),
    (
        "platform",
        ["platform", "architecture", "technical"],
        ["architecture overview", "technical deep dive"],
    ),
    ("architecture", ["architecture", "technical"], ["architecture overview", "integration"]),
    ("integration", ["integration", "technical"], ["integration", "architecture overview"]),
    ("analytics", ["analytics", "platform"], ["analytics", "platform overview"]),
    ("snowflake", ["snowflake", "data", "integration"], ["data sharing", "integration"]),
    ("api", ["api", "technical", "demo"], ["api demo", "technical deep dive"]),
    ("data", ["data", "demo"], ["data demo"]),
    ("overview", ["overview"], ["overview"]),
    ("demo", ["demo"], ["demo"]),
    ("ingestion", ["ingestion", "data", "demo"], ["data ingestion demo"]),
    ("orchestration", ["orchestration", "data"], ["data orchestration"]),
    ("value", ["value", "sales"], ["value proposition"]),
    ("use case", ["use-case", "platform"], ["use case review"]),
]


def _registry_path() -> Path:
    """Get the default registry file path."""
    cfg = get_config()
    system_dir = cfg.vault_path / "90_System"
    system_dir.mkdir(parents=True, exist_ok=True)
    return system_dir / "template_registry.yaml"


def _make_id(filename: str) -> str:
    """Derive a template ID from filename."""
    stem = Path(filename).stem
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return slug


def _infer_metadata(filepath: Path, templates_root: Path) -> tuple[list[str], list[str], list[str]]:
    """Infer tags, use_cases, domains from filename and path."""
    name_lower = filepath.stem.lower()
    parent_lower = filepath.parent.name.lower()

    tags: list[str] = []
    use_cases: list[str] = []
    domains: list[str] = []

    for keyword, ktags, kuses in _AUTO_TAGS:
        if keyword in name_lower or keyword in parent_lower:
            tags.extend(ktags)
            use_cases.extend(kuses)

    # Domain inference
    if any(t in tags for t in ["demo", "api", "data", "ingestion"]):
        domains.append("Technical")
    if any(
        t in tags
        for t in [
            "corporate",
            "pitch",
            "overview",
            "value",
            "discovery",
            "questions",
        ]
    ):
        domains.append("Go-to-Market")
    if any(t in tags for t in ["platform", "architecture", "integration"]):
        domains.extend(["Product", "Technical"])

    # Subfolder-based type hints
    if "demo scripts" in parent_lower:
        tags.append("demo-script")
        use_cases.append("demo")

    if "prepare presentation" in parent_lower:
        if "presentation" not in tags:
            tags.append("presentation")

    # Deduplicate
    tags = list(dict.fromkeys(tags))
    use_cases = list(dict.fromkeys(use_cases))
    domains = list(dict.fromkeys(domains))

    return tags, use_cases, domains


def _infer_type(filepath: Path) -> str:
    """Infer template type from extension and location."""
    ext = filepath.suffix.lower()
    parent_lower = filepath.parent.name.lower()

    # CSV/JSON in Demo Scripts are data files
    if ext in {".csv", ".json"}:
        return "data"
    if "demo scripts" in parent_lower and ext == ".docx":
        return "demo_script"

    return _TYPE_BY_EXT.get(ext, "document")


# --- Public API ---


def scan_templates(templates_root: Path | None = None) -> list[TemplateInfo]:
    """Walk 30_Templates/, find template files, build TemplateInfo list.

    Skips Deprecated/, Working/, Canonical/ (empty management dirs).
    Includes .pptx, .xlsx, .docx, .csv, .json files.
    """
    if templates_root is None:
        cfg = get_config()
        templates_root = cfg.templates_root

    if not templates_root.exists():
        logger.warning("Templates root not found: %s", templates_root)
        return []

    all_extensions = _TEMPLATE_EXTENSIONS | {".csv", ".json"}
    templates: list[TemplateInfo] = []

    for filepath in sorted(templates_root.rglob("*")):
        if not filepath.is_file():
            continue
        if filepath.suffix.lower() not in all_extensions:
            continue
        # Skip files starting with ~$ (Office temp files)
        if filepath.name.startswith("~$"):
            continue

        # Skip management directories
        rel = filepath.relative_to(templates_root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue

        tags, use_cases, domains = _infer_metadata(filepath, templates_root)
        tpl_type = _infer_type(filepath)

        size_mb = round(filepath.stat().st_size / (1024 * 1024), 3)
        rel_path = f"30_Templates/{rel.as_posix()}"

        templates.append(
            TemplateInfo(
                id=_make_id(filepath.name),
                name=filepath.stem.replace("_", " "),
                file=filepath.name,
                path=rel_path,
                size_mb=size_mb,
                type=tpl_type,
                use_cases=use_cases,
                domains=domains,
                tags=tags,
                language="en",
            )
        )

    return templates


def load_registry(registry_path: Path | None = None) -> list[TemplateInfo]:
    """Load template_registry.yaml."""
    if registry_path is None:
        registry_path = _registry_path()

    if not registry_path.exists():
        return []

    try:
        with open(registry_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("Failed to load template registry: %s", e)
        return []

    if not data or "templates" not in data:
        return []

    templates: list[TemplateInfo] = []
    for item in data["templates"]:
        templates.append(
            TemplateInfo(
                id=item["id"],
                name=item.get("name", ""),
                file=item.get("file", ""),
                path=item.get("path", ""),
                size_mb=float(item.get("size_mb", 0)),
                type=item.get("type", "document"),
                use_cases=item.get("use_cases", []),
                domains=item.get("domains", []),
                tags=item.get("tags", []),
                language=item.get("language", "en"),
            )
        )

    return templates


def save_registry(
    templates: list[TemplateInfo],
    registry_path: Path | None = None,
) -> Path:
    """Write template_registry.yaml."""
    if registry_path is None:
        registry_path = _registry_path()

    data = {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "file": t.file,
                "path": t.path,
                "size_mb": t.size_mb,
                "type": t.type,
                "use_cases": t.use_cases,
                "domains": t.domains,
                "tags": t.tags,
                "language": t.language,
            }
            for t in templates
        ],
    }

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("Saved registry with %d templates to %s", len(templates), registry_path)
    return registry_path


def select_template(
    goal: str,
    templates: list[TemplateInfo] | None = None,
) -> TemplateInfo | None:
    """Select best template for a goal via keyword matching.

    Scores each template by number of matching tags + use_cases.
    Returns None if templates list is empty.

    Args:
        goal: User's description of what they need (e.g. "demo", "architecture overview").
        templates: Template list. If None, loads from registry.
    """
    if templates is None:
        templates = load_registry()

    if not templates:
        return None

    goal_lower = goal.lower()
    goal_words = set(re.split(r"\W+", goal_lower))

    scored: list[tuple[int, TemplateInfo]] = []

    for tpl in templates:
        score = 0
        searchable = tpl.tags + tpl.use_cases + tpl.domains

        for term in searchable:
            term_lower = term.lower()
            # Exact phrase match in goal string
            if term_lower in goal_lower:
                score += 2
            # Word overlap
            term_words = set(re.split(r"\W+", term_lower))
            score += len(goal_words & term_words)

        if score > 0:
            scored.append((score, tpl))

    if not scored:
        # Fallback: prefer presentations, largest file (the main corporate deck)
        presentations = [t for t in templates if t.type == "presentation"]
        if presentations:
            return max(presentations, key=lambda t: t.size_mb)
        return templates[0]

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def copy_template(
    template: TemplateInfo,
    destination: Path,
    new_name: str,
) -> Path:
    """Copy template file to destination with new filename.

    Args:
        template: Template to copy.
        destination: Target directory.
        new_name: New filename (including extension).

    Returns:
        Path to the copied file.
    """
    cfg = get_config()
    # Resolve absolute source path from relative path
    # path is like "30_Templates/subdir/file.pptx"
    # templates_root is the 30_Templates dir itself
    rel_within = template.path.replace("30_Templates/", "", 1)
    source = cfg.templates_root / rel_within

    if not source.exists():
        msg = f"Template file not found: {source}"
        raise FileNotFoundError(msg)

    destination.mkdir(parents=True, exist_ok=True)
    dest_file = destination / new_name
    shutil.copy2(str(source), str(dest_file))
    logger.info("Copied template %s -> %s", source.name, dest_file)
    return dest_file
