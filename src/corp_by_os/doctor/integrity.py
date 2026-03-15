"""System integrity checks for Corporate OS.

Verifies consistency between registry, filesystem, ops.db, vault, and index.
Each check function appends issues to the report and increments counters.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class IntegrityIssue:
    """A single integrity issue found."""

    category: str  # 'registry' | 'filesystem' | 'ops_db' | 'vault' | 'index' | 'config'
    severity: str  # 'error' | 'warning' | 'info'
    description: str
    path: str | None = None
    fix_hint: str | None = None


@dataclass
class IntegrityReport:
    """Full integrity check results."""

    checked_at: str = ""
    issues: list[IntegrityIssue] = field(default_factory=list)
    checks_passed: int = 0
    checks_failed: int = 0
    checks_warned: int = 0

    @property
    def healthy(self) -> bool:
        return self.checks_failed == 0


def check_all(
    mywork_root: Path,
    vault_root: Path,
    index_db_path: Path,
    ops_db_path: Path,
    registry_path: Path,
    routing_map_path: Path,
) -> IntegrityReport:
    """Run all integrity checks. Returns IntegrityReport."""
    report = IntegrityReport(checked_at=datetime.now().isoformat())

    _check_config_files(report, registry_path, routing_map_path)
    _check_registry_paths(report, mywork_root, registry_path)
    _check_ops_db(report, mywork_root, ops_db_path)
    _check_vault_index(report, vault_root, index_db_path)
    _check_mywork_structure(report, mywork_root)
    _check_inbox(report, mywork_root)

    return report


def _check_config_files(
    report: IntegrityReport,
    registry_path: Path,
    routing_map_path: Path,
) -> None:
    """Verify config files exist and parse correctly."""
    import yaml

    # routing_map.yaml
    if not routing_map_path.exists():
        report.issues.append(
            IntegrityIssue(
                category="config",
                severity="error",
                description="routing_map.yaml missing",
                path=str(routing_map_path),
                fix_hint="Run Phase 0 setup or restore from git",
            )
        )
        report.checks_failed += 1
    else:
        try:
            with open(routing_map_path, encoding="utf-8") as f:
                yaml.safe_load(f)
            report.checks_passed += 1
        except Exception as exc:
            report.issues.append(
                IntegrityIssue(
                    category="config",
                    severity="error",
                    description=f"routing_map.yaml parse error: {exc}",
                    path=str(routing_map_path),
                )
            )
            report.checks_failed += 1

    # content_registry.yaml
    if not registry_path.exists():
        report.issues.append(
            IntegrityIssue(
                category="config",
                severity="error",
                description="content_registry.yaml missing",
                path=str(registry_path),
                fix_hint="Copy from config/content_registry.yaml in repo",
            )
        )
        report.checks_failed += 1
    else:
        try:
            with open(registry_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or "series" not in data:
                report.issues.append(
                    IntegrityIssue(
                        category="config",
                        severity="warning",
                        description='content_registry.yaml missing "series" section',
                        path=str(registry_path),
                    )
                )
                report.checks_warned += 1
            else:
                report.checks_passed += 1
        except Exception as exc:
            report.issues.append(
                IntegrityIssue(
                    category="config",
                    severity="error",
                    description=f"content_registry.yaml parse error: {exc}",
                    path=str(registry_path),
                )
            )
            report.checks_failed += 1


def _check_registry_paths(
    report: IntegrityReport,
    mywork_root: Path,
    registry_path: Path,
) -> None:
    """Verify that destinations in registry actually exist on filesystem."""
    import yaml

    if not registry_path.exists():
        return  # Already reported in config check

    try:
        with open(registry_path, encoding="utf-8") as f:
            registry = yaml.safe_load(f)
    except Exception:
        return

    if not isinstance(registry, dict):
        return

    # Check series destinations
    for series_id, series in registry.get("series", {}).items():
        dest = series.get("destination", "")
        if not dest:
            continue
        dest_path = mywork_root / dest
        if not dest_path.exists():
            report.issues.append(
                IntegrityIssue(
                    category="registry",
                    severity="warning",
                    description=f'Series "{series_id}" destination missing: {dest}',
                    path=dest,
                    fix_hint=f'Create folder: mkdir "{dest_path}"',
                )
            )
            report.checks_warned += 1
        else:
            report.checks_passed += 1

    # Check destination rules
    for rule in registry.get("destination_rules", []):
        dest = rule.get("destination", "")
        if not dest or "{" in dest:
            continue
        dest_path = mywork_root / dest
        if not dest_path.exists():
            report.issues.append(
                IntegrityIssue(
                    category="registry",
                    severity="warning",
                    description=f'Rule "{rule.get("name", "unnamed")}" destination missing: {dest}',
                    path=dest,
                    fix_hint=f'Create folder: mkdir "{dest_path}"',
                )
            )
            report.checks_warned += 1
        else:
            report.checks_passed += 1


def _check_ops_db(
    report: IntegrityReport,
    mywork_root: Path,
    ops_db_path: Path,
) -> None:
    """Verify ops.db consistency — assets point to real files."""
    if not ops_db_path.exists():
        report.issues.append(
            IntegrityIssue(
                category="ops_db",
                severity="info",
                description="ops.db does not exist yet (no ingest has run)",
                path=str(ops_db_path),
            )
        )
        report.checks_passed += 1
        return

    try:
        conn = sqlite3.connect(str(ops_db_path))
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT path, status FROM assets WHERE status != 'deleted'",
        ).fetchall()

        missing_count = 0
        for row in rows:
            file_path = mywork_root / row["path"]
            if not file_path.exists():
                missing_count += 1
                if missing_count <= 5:
                    report.issues.append(
                        IntegrityIssue(
                            category="ops_db",
                            severity="warning",
                            description=(
                                f"Asset in ops.db not found on disk: "
                                f"{row['path']} (status: {row['status']})"
                            ),
                            path=row["path"],
                            fix_hint=(
                                "File was moved/deleted outside corp-by-os. Update or re-scan."
                            ),
                        )
                    )

        if missing_count > 5:
            report.issues.append(
                IntegrityIssue(
                    category="ops_db",
                    severity="warning",
                    description=f"...and {missing_count - 5} more assets missing from disk",
                )
            )

        if missing_count > 0:
            report.checks_warned += 1
        else:
            report.checks_passed += 1

        # Check for stale pending items
        pending = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE status = 'pending'",
        ).fetchone()[0]
        if pending > 0:
            report.issues.append(
                IntegrityIssue(
                    category="ops_db",
                    severity="info",
                    description=f"{pending} assets still pending in ops.db",
                    fix_hint="Run corp overnight or corp ingest to process them",
                )
            )

        conn.close()
    except Exception as exc:
        report.issues.append(
            IntegrityIssue(
                category="ops_db",
                severity="error",
                description=f"ops.db read error: {exc}",
                path=str(ops_db_path),
            )
        )
        report.checks_failed += 1


def _check_vault_index(
    report: IntegrityReport,
    vault_root: Path,
    index_db_path: Path,
) -> None:
    """Verify index.db is in sync with vault notes."""
    if not index_db_path.exists():
        report.issues.append(
            IntegrityIssue(
                category="index",
                severity="error",
                description="index.db missing",
                path=str(index_db_path),
                fix_hint="Run: corp index rebuild",
            )
        )
        report.checks_failed += 1
        return

    try:
        conn = sqlite3.connect(str(index_db_path))

        index_count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

        # Count actual vault notes
        skip_names = {"synthesis.md", "index.md"}
        vault_count = 0
        for scan_dir in [vault_root / "02_sources", vault_root / "04_evergreen"]:
            if scan_dir.exists():
                vault_count += sum(1 for f in scan_dir.rglob("*.md") if f.name not in skip_names)

        drift = abs(index_count - vault_count)
        if drift > 50:
            report.issues.append(
                IntegrityIssue(
                    category="index",
                    severity="warning",
                    description=(
                        f"Index drift: {index_count} indexed vs "
                        f"{vault_count} vault notes (diff: {drift})"
                    ),
                    fix_hint="Run: corp index rebuild",
                )
            )
            report.checks_warned += 1
        elif drift > 0:
            report.issues.append(
                IntegrityIssue(
                    category="index",
                    severity="info",
                    description=(
                        f"Minor index drift: {index_count} indexed vs "
                        f"{vault_count} vault notes (diff: {drift})"
                    ),
                    fix_hint="Run: corp index rebuild",
                )
            )
            report.checks_passed += 1
        else:
            report.checks_passed += 1

        conn.close()
    except Exception as exc:
        report.issues.append(
            IntegrityIssue(
                category="index",
                severity="error",
                description=f"index.db read error: {exc}",
                path=str(index_db_path),
            )
        )
        report.checks_failed += 1


def _check_mywork_structure(
    report: IntegrityReport,
    mywork_root: Path,
) -> None:
    """Verify MyWork folder structure is intact."""
    required_folders = [
        "00_Inbox",
        "10_Projects",
        "20_Extra_Initiatives",
        "30_Templates",
        "50_RFP",
        "60_Source_Library",
        "70_Admin",
        "90_System",
    ]

    for folder in required_folders:
        folder_path = mywork_root / folder
        if not folder_path.exists():
            report.issues.append(
                IntegrityIssue(
                    category="filesystem",
                    severity="error",
                    description=f"Required MyWork folder missing: {folder}",
                    path=folder,
                    fix_hint=f'Create: mkdir "{folder_path}"',
                )
            )
            report.checks_failed += 1
        else:
            report.checks_passed += 1


def _check_inbox(
    report: IntegrityReport,
    mywork_root: Path,
) -> None:
    """Check Inbox health — should be empty or near-empty."""
    inbox = mywork_root / "00_Inbox"
    if not inbox.exists():
        return

    skip_names = {
        "_triage_log.jsonl",
        "_triage_schema.yaml",
        "folder_manifest.yaml",
    }
    skip_dirs = {"_Unmatched", "_Staging"}

    files: list[Path] = []
    for item in inbox.iterdir():
        if item.name in skip_names or item.name.startswith("."):
            continue
        if item.is_dir() and item.name in skip_dirs:
            quarantined = [x for x in item.rglob("*") if x.is_file()]
            if quarantined:
                report.issues.append(
                    IntegrityIssue(
                        category="filesystem",
                        severity="info",
                        description=(f"{len(quarantined)} files in {item.name}/ awaiting review"),
                        path=f"00_Inbox/{item.name}",
                        fix_hint="Run: corp classify or corp finalize",
                    )
                )
            continue
        files.append(item)

    if files:
        report.issues.append(
            IntegrityIssue(
                category="filesystem",
                severity="info",
                description=f"{len(files)} file(s) in Inbox awaiting ingest",
                path="00_Inbox",
                fix_hint="Run: corp ingest",
            )
        )
