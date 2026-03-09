"""Cross-project SQLite index.

Aggregates project-info.yaml + facts.yaml from all projects into a single
queryable database. Lives in %LOCALAPPDATA%/corp-by-os/index.db.

NOT in OneDrive — rebuilds are frequent, SQLite + OneDrive sync = corruption.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from corp_by_os.config import get_config
from corp_by_os.models import IndexStats

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    status TEXT,
    products TEXT,
    topics TEXT,
    domains TEXT,
    people TEXT,
    region TEXT,
    industry TEXT,
    files_processed INTEGER DEFAULT 0,
    facts_count INTEGER DEFAULT 0,
    last_extracted TEXT,
    onedrive_path TEXT,
    vault_path TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    source TEXT,
    source_title TEXT,
    topics TEXT,
    domains TEXT,
    products TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    fact, source_title, topics, project_id,
    content=facts, content_rowid=id
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Triggers to keep FTS in sync with facts table
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, fact, source_title, topics, project_id)
    VALUES (new.id, new.fact, new.source_title, new.topics, new.project_id);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, fact, source_title, topics, project_id)
    VALUES ('delete', old.id, old.fact, old.source_title, old.topics, old.project_id);
END;
"""


def get_index_path() -> Path:
    """Returns %LOCALAPPDATA%/corp-by-os/index.db."""
    cfg = get_config()
    return cfg.app_data_path / "index.db"


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the index database."""
    if db_path is None:
        db_path = get_index_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript(_SCHEMA)


def rebuild_index(db_path: Path | None = None) -> IndexStats:
    """Full rebuild: scan vault + OneDrive, aggregate into SQLite."""
    start = time.time()
    cfg = get_config()
    conn = _connect(db_path)

    try:
        _ensure_schema(conn)

        # Clear existing data
        conn.execute("DELETE FROM facts")
        conn.execute("DELETE FROM projects")

        projects_count = 0
        facts_count = 0

        # Collect all project folders from OneDrive + vault
        project_dirs = _collect_project_dirs(cfg)

        for pid, info in project_dirs.items():
            _insert_project(conn, pid, info)
            projects_count += 1

            # Load facts
            n = _load_and_insert_facts(conn, pid, info)
            facts_count += n

            # Update facts_count on the project row
            if n > 0:
                conn.execute(
                    "UPDATE projects SET facts_count = ? WHERE project_id = ?",
                    (n, pid),
                )

        # Rebuild FTS
        conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")

        # Update meta
        now = datetime.now().isoformat(timespec="seconds")
        duration = time.time() - start
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('last_rebuild', ?)", (now,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('total_projects', ?)",
            (str(projects_count),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('total_facts', ?)",
            (str(facts_count),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('rebuild_duration_seconds', ?)",
            (f"{duration:.2f}",),
        )
        conn.commit()

        path = db_path or get_index_path()
        logger.info(
            "Index rebuilt: %d projects, %d facts in %.1fs -> %s",
            projects_count, facts_count, duration, path,
        )

        return IndexStats(
            projects_indexed=projects_count,
            facts_indexed=facts_count,
            rebuild_duration=duration,
            index_path=str(path),
        )
    finally:
        conn.close()


def update_project(project_id: str, db_path: Path | None = None) -> bool:
    """Update a single project in the index."""
    cfg = get_config()
    conn = _connect(db_path)

    try:
        _ensure_schema(conn)

        # Remove old data for this project
        conn.execute("DELETE FROM facts WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))

        project_dirs = _collect_project_dirs(cfg)
        info = project_dirs.get(project_id)
        if info is None:
            conn.commit()
            return False

        _insert_project(conn, project_id, info)
        n = _load_and_insert_facts(conn, project_id, info)
        if n > 0:
            conn.execute(
                "UPDATE projects SET facts_count = ? WHERE project_id = ?",
                (n, project_id),
            )

        # Rebuild FTS for consistency
        conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
        conn.commit()
        return True
    finally:
        conn.close()


def get_index_stats(db_path: Path | None = None) -> dict[str, str]:
    """Read meta table for index stats."""
    path = db_path or get_index_path()
    if not path.exists():
        return {}
    conn = _connect(db_path)
    try:
        _ensure_schema(conn)
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return dict(rows)
    finally:
        conn.close()


# --- Internal helpers ---


def _collect_project_dirs(cfg) -> dict[str, dict]:
    """Merge OneDrive + vault project directories into unified dict.

    Returns {project_id: {client, status, onedrive_path, vault_path, ...}}
    """
    projects: dict[str, dict] = {}

    # Scan OneDrive
    if cfg.projects_root.exists():
        for folder in sorted(cfg.projects_root.iterdir()):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                pid = folder.name.lower()
                client = folder.name.split("_")[0]
                projects[pid] = {
                    "client": client,
                    "status": "unknown",
                    "onedrive_path": str(folder),
                    "vault_path": None,
                    "products": [],
                    "topics": [],
                    "domains": [],
                    "people": [],
                    "region": None,
                    "industry": None,
                    "files_processed": 0,
                    "facts_count": 0,
                    "last_extracted": None,
                }
                # Try to read project-info.yaml from _knowledge/
                _enrich_from_onedrive(projects[pid], folder)

    # Scan vault
    from corp_by_os.models import VaultZone
    vault_projects = cfg.vault_path / VaultZone.PROJECTS.value
    if vault_projects.exists():
        for folder in sorted(vault_projects.iterdir()):
            if folder.is_dir() and not folder.name.startswith((".", "_")):
                pid = folder.name.lower()
                if pid not in projects:
                    projects[pid] = {
                        "client": folder.name.split("_")[0],
                        "status": "unknown",
                        "onedrive_path": None,
                        "vault_path": str(folder),
                        "products": [],
                        "topics": [],
                        "domains": [],
                        "people": [],
                        "region": None,
                        "industry": None,
                        "files_processed": 0,
                        "facts_count": 0,
                        "last_extracted": None,
                    }
                else:
                    projects[pid]["vault_path"] = str(folder)

                # Enrich from vault project-info.yaml (overrides OneDrive)
                _enrich_from_vault(projects[pid], folder)

    return projects


def _enrich_from_onedrive(info: dict, folder: Path) -> None:
    """Read _knowledge/project-info.yaml from OneDrive folder."""
    info_file = folder / "_knowledge" / "project-info.yaml"
    if not info_file.exists():
        return
    try:
        with open(info_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return
        info["status"] = data.get("status", info["status"])
        info["products"] = data.get("products", [])
        info["topics"] = data.get("topics", [])
        info["people"] = data.get("people", [])
        info["files_processed"] = data.get("files_processed", 0)
        info["last_extracted"] = data.get("rendered_at", None)
        # Opportunity metadata
        opp = data.get("opportunity", {})
        if isinstance(opp, dict):
            info["region"] = opp.get("region", info.get("region"))
            info["industry"] = opp.get("industry", info.get("industry"))
    except Exception as e:
        logger.debug("Failed to read OneDrive project-info: %s", e)


def _enrich_from_vault(info: dict, folder: Path) -> None:
    """Read project-info.yaml from vault folder."""
    info_file = folder / "project-info.yaml"
    if not info_file.exists():
        return
    try:
        with open(info_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return
        info["client"] = data.get("client", info["client"])
        info["status"] = data.get("status", info["status"])
        info["products"] = data.get("products", info["products"])
        info["topics"] = data.get("topics", info["topics"])
        info["domains"] = data.get("domains", [])
        info["people"] = data.get("people", info["people"])
        info["region"] = data.get("region", info.get("region"))
        info["industry"] = data.get("industry", info.get("industry"))
        info["files_processed"] = data.get("files_processed", info["files_processed"])
        info["facts_count"] = data.get("facts_count", 0)
        info["last_extracted"] = data.get("last_extracted", info.get("last_extracted"))
    except Exception as e:
        logger.debug("Failed to read vault project-info: %s", e)


def _insert_project(conn: sqlite3.Connection, pid: str, info: dict) -> None:
    """Insert a project row."""
    conn.execute(
        """INSERT OR REPLACE INTO projects
           (project_id, client, status, products, topics, domains, people,
            region, industry, files_processed, facts_count,
            last_extracted, onedrive_path, vault_path, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            info["client"],
            info["status"],
            json.dumps(info.get("products", [])),
            json.dumps(info.get("topics", [])),
            json.dumps(info.get("domains", [])),
            json.dumps(info.get("people", [])),
            info.get("region"),
            info.get("industry"),
            info.get("files_processed", 0),
            info.get("facts_count", 0),
            info.get("last_extracted"),
            info.get("onedrive_path"),
            info.get("vault_path"),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def _load_and_insert_facts(
    conn: sqlite3.Connection,
    pid: str,
    info: dict,
) -> int:
    """Load facts.yaml for a project and insert into DB. Returns count."""
    facts_paths = []

    # Check vault first
    if info.get("vault_path"):
        vault_facts = Path(info["vault_path"]) / "facts.yaml"
        if vault_facts.exists():
            facts_paths.append(vault_facts)

    # Fallback to OneDrive _knowledge/
    if not facts_paths and info.get("onedrive_path"):
        od_facts = Path(info["onedrive_path"]) / "_knowledge" / "facts.yaml"
        if od_facts.exists():
            facts_paths.append(od_facts)

    if not facts_paths:
        return 0

    total = 0
    for facts_path in facts_paths:
        try:
            with open(facts_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            logger.debug("Failed to load facts from %s: %s", facts_path, e)
            continue

        if not isinstance(data, dict):
            continue

        facts_list = data.get("facts", [])
        if not isinstance(facts_list, list):
            continue

        for fact_item in facts_list:
            if not isinstance(fact_item, dict):
                continue

            fact_text = fact_item.get("fact", fact_item.get("text", ""))
            if not fact_text:
                continue

            topics = fact_item.get("topics", [])
            domains = fact_item.get("domains", [])
            products = fact_item.get("products", [])

            conn.execute(
                """INSERT INTO facts
                   (project_id, fact, source, source_title, topics, domains, products)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    fact_text,
                    fact_item.get("source", ""),
                    fact_item.get("source_title", ""),
                    json.dumps(topics) if isinstance(topics, list) else str(topics),
                    json.dumps(domains) if isinstance(domains, list) else str(domains),
                    json.dumps(products) if isinstance(products, list) else str(products),
                ),
            )
            total += 1

    return total
