"""Operational database for asset tracking and ingest audit trail.

ops.db is the system's memory of what files exist, where they went,
and why. Every state change is logged as an ingest_event for undo support.

DB location: {app_data_path}/ops.db (alongside index.db, NOT in OneDrive).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """\
-- Assets: every known file in MyWork (populated by scans)
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime TEXT NOT NULL,
    content_hash TEXT,
    folder_l1 TEXT NOT NULL,
    folder_l2 TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    package_id INTEGER,
    routed_to TEXT,
    routed_method TEXT,
    routed_confidence REAL,
    extracted_note_path TEXT,
    source_hash_at_extraction TEXT,
    first_seen TEXT NOT NULL,
    last_scanned TEXT NOT NULL,
    FOREIGN KEY (package_id) REFERENCES packages(id)
);

CREATE INDEX IF NOT EXISTS idx_assets_path ON assets(path);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_folder_l1 ON assets(folder_l1);
CREATE INDEX IF NOT EXISTS idx_assets_extension ON assets(extension);

-- Packages: folder-level groupings (for folder ingest)
CREATE TABLE IF NOT EXISTS packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    destination_path TEXT,
    inferred_topic TEXT,
    inferred_products TEXT,
    inferred_domains TEXT,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

-- Ingest events: audit trail of every action taken on an asset
CREATE TABLE IF NOT EXISTS ingest_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER,
    package_id INTEGER,
    action TEXT NOT NULL,
    source_path TEXT,
    destination_path TEXT,
    method TEXT,
    confidence REAL,
    reasoning TEXT,
    cost REAL DEFAULT 0.0,
    timestamp TEXT NOT NULL,
    reversible INTEGER NOT NULL DEFAULT 1,
    reverted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (asset_id) REFERENCES assets(id),
    FOREIGN KEY (package_id) REFERENCES packages(id)
);

CREATE INDEX IF NOT EXISTS idx_events_asset ON ingest_events(asset_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON ingest_events(timestamp);

-- Registry suggestions: auto-discovered patterns from scans
CREATE TABLE IF NOT EXISTS registry_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    proposed_series TEXT NOT NULL,
    proposed_destination TEXT,
    evidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
"""


def get_ops_db_path() -> Path:
    """Default ops.db path from config."""
    from corp_by_os.config import get_config

    return get_config().app_data_path / "ops.db"


class OpsDB:
    """Operational database for asset tracking and ingest audit trail.

    This is the system's memory of what files exist, where they went,
    and why. Every action is logged as an ingest_event for undo support.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_ops_db_path()
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # === Timestamp helper ===

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # === Asset operations ===

    def upsert_asset(
        self,
        path: str,
        filename: str,
        extension: str,
        size_bytes: int,
        mtime: str,
        folder_l1: str,
        folder_l2: str | None = None,
    ) -> int:
        """Insert or update an asset record. Returns asset ID.

        Path must use forward slashes, relative to mywork_root.
        """
        # Normalize path to forward slashes
        path = path.replace("\\", "/")
        now = self._now()

        existing = self.conn.execute(
            "SELECT id FROM assets WHERE path = ?", (path,),
        ).fetchone()

        if existing:
            self.conn.execute(
                """UPDATE assets SET
                     filename = ?, extension = ?, size_bytes = ?,
                     mtime = ?, folder_l1 = ?, folder_l2 = ?,
                     last_scanned = ?
                   WHERE path = ?""",
                (filename, extension, size_bytes, mtime, folder_l1,
                 folder_l2, now, path),
            )
            self.conn.commit()
            return existing["id"]

        cur = self.conn.execute(
            """INSERT INTO assets
               (path, filename, extension, size_bytes, mtime,
                folder_l1, folder_l2, first_seen, last_scanned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (path, filename, extension, size_bytes, mtime,
             folder_l1, folder_l2, now, now),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_asset(self, path: str) -> dict | None:
        """Get asset by relative path."""
        path = path.replace("\\", "/")
        row = self.conn.execute(
            "SELECT * FROM assets WHERE path = ?", (path,),
        ).fetchone()
        return dict(row) if row else None

    def get_assets_by_status(self, status: str) -> list[dict]:
        """Get all assets with given status."""
        rows = self.conn.execute(
            "SELECT * FROM assets WHERE status = ?", (status,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_assets_by_folder(self, folder_l1: str) -> list[dict]:
        """Get all assets in a given L1 folder."""
        rows = self.conn.execute(
            "SELECT * FROM assets WHERE folder_l1 = ?", (folder_l1,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_asset_path(self, old_path: str, new_path: str) -> bool:
        """Update the canonical path of an asset after a file move.

        Must be called BEFORE update_asset_status when the lookup path
        needs to reflect the file's new location. Returns True if a row
        was updated.
        """
        old_path = old_path.replace("\\", "/")
        new_path = new_path.replace("\\", "/")
        cur = self.conn.execute(
            "UPDATE assets SET path = ? WHERE path = ?",
            (new_path, old_path),
        )
        self.conn.commit()
        if cur.rowcount == 0:
            logger.warning(
                "update_asset_path: no asset found at old path: %s", old_path,
            )
            return False
        return True

    def update_asset_status(
        self,
        path: str,
        status: str,
        *,
        routed_to: str | None = None,
        routed_method: str | None = None,
        routed_confidence: float | None = None,
        extracted_note_path: str | None = None,
        source_hash_at_extraction: str | None = None,
        package_id: int | None = None,
        reasoning: str | None = None,
        cost: float = 0.0,
    ) -> None:
        """Update asset status and optional fields. Logs an ingest_event.

        Every state change goes through here to maintain the audit trail.
        """
        path = path.replace("\\", "/")
        asset = self.get_asset(path)
        if asset is None:
            logger.warning("Cannot update status: asset not found: %s", path)
            return

        parts = ["status = ?"]
        params: list = [status]

        if routed_to is not None:
            parts.append("routed_to = ?")
            params.append(routed_to.replace("\\", "/"))
        if routed_method is not None:
            parts.append("routed_method = ?")
            params.append(routed_method)
        if routed_confidence is not None:
            parts.append("routed_confidence = ?")
            params.append(routed_confidence)
        if extracted_note_path is not None:
            parts.append("extracted_note_path = ?")
            params.append(extracted_note_path.replace("\\", "/"))
        if source_hash_at_extraction is not None:
            parts.append("source_hash_at_extraction = ?")
            params.append(source_hash_at_extraction)
        if package_id is not None:
            parts.append("package_id = ?")
            params.append(package_id)

        params.append(path)
        sql = f"UPDATE assets SET {', '.join(parts)} WHERE path = ?"
        self.conn.execute(sql, params)

        # Log the ingest event
        self.log_event(
            action=status,
            asset_id=asset["id"],
            source_path=path,
            destination_path=routed_to,
            method=routed_method,
            confidence=routed_confidence,
            reasoning=reasoning,
            cost=cost,
        )

        self.conn.commit()

    # === Package operations ===

    def create_package(
        self,
        folder_name: str,
        source_path: str,
        file_count: int,
        total_size: int,
        *,
        inferred_topic: str | None = None,
        inferred_products: str | None = None,
        inferred_domains: str | None = None,
    ) -> int:
        """Create a new package record. Returns package ID."""
        source_path = source_path.replace("\\", "/")
        cur = self.conn.execute(
            """INSERT INTO packages
               (folder_name, source_path, file_count, total_size_bytes,
                inferred_topic, inferred_products, inferred_domains, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (folder_name, source_path, file_count, total_size,
             inferred_topic, inferred_products, inferred_domains, self._now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_package(self, package_id: int) -> dict | None:
        """Get package by ID."""
        row = self.conn.execute(
            "SELECT * FROM packages WHERE id = ?", (package_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_package_status(
        self,
        package_id: int,
        status: str,
        *,
        destination_path: str | None = None,
    ) -> None:
        """Update package status."""
        parts = ["status = ?"]
        params: list = [status]

        if destination_path is not None:
            parts.append("destination_path = ?")
            params.append(destination_path.replace("\\", "/"))
        if status in ("extracted", "archived"):
            parts.append("completed_at = ?")
            params.append(self._now())

        params.append(package_id)
        sql = f"UPDATE packages SET {', '.join(parts)} WHERE id = ?"
        self.conn.execute(sql, params)
        self.conn.commit()

    # === Ingest event operations ===

    def log_event(
        self,
        action: str,
        *,
        asset_id: int | None = None,
        package_id: int | None = None,
        source_path: str | None = None,
        destination_path: str | None = None,
        method: str | None = None,
        confidence: float | None = None,
        reasoning: str | None = None,
        cost: float = 0.0,
        reversible: bool = True,
    ) -> int:
        """Log an ingest event. Returns event ID.

        EVERY state change must go through this method to maintain
        the audit trail and enable undo.
        """
        if source_path:
            source_path = source_path.replace("\\", "/")
        if destination_path:
            destination_path = destination_path.replace("\\", "/")

        cur = self.conn.execute(
            """INSERT INTO ingest_events
               (asset_id, package_id, action, source_path, destination_path,
                method, confidence, reasoning, cost, timestamp, reversible)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, package_id, action, source_path, destination_path,
             method, confidence, reasoning, cost, self._now(),
             1 if reversible else 0),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def revert_event(self, event_id: int) -> bool:
        """Mark an event as reverted.

        Does NOT undo the filesystem change — that's the caller's
        responsibility. This just marks the record.
        """
        row = self.conn.execute(
            "SELECT reversible, reverted FROM ingest_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return False
        if not row["reversible"]:
            logger.warning("Event %d is not reversible", event_id)
            return False
        if row["reverted"]:
            logger.warning("Event %d already reverted", event_id)
            return False

        self.conn.execute(
            "UPDATE ingest_events SET reverted = 1 WHERE id = ?",
            (event_id,),
        )
        self.conn.commit()
        return True

    def get_events_for_asset(self, asset_id: int) -> list[dict]:
        """Get full history for an asset, ordered chronologically."""
        rows = self.conn.execute(
            "SELECT * FROM ingest_events WHERE asset_id = ? ORDER BY timestamp",
            (asset_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """Get most recent ingest events."""
        rows = self.conn.execute(
            "SELECT * FROM ingest_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # === Registry suggestion operations ===

    def add_suggestion(
        self,
        pattern: str,
        proposed_series: str,
        proposed_destination: str,
        evidence: str,
    ) -> int:
        """Add a registry suggestion from auto-discovery."""
        cur = self.conn.execute(
            """INSERT INTO registry_suggestions
               (pattern, proposed_series, proposed_destination, evidence, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (pattern, proposed_series, proposed_destination, evidence,
             self._now()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_pending_suggestions(self) -> list[dict]:
        """Get suggestions awaiting review."""
        rows = self.conn.execute(
            "SELECT * FROM registry_suggestions WHERE status = 'pending' ORDER BY created_at",
        ).fetchall()
        return [dict(r) for r in rows]

    def update_suggestion_status(
        self,
        suggestion_id: int,
        status: str,
    ) -> None:
        """Approve, reject, or expire a suggestion."""
        self.conn.execute(
            "UPDATE registry_suggestions SET status = ?, reviewed_at = ? WHERE id = ?",
            (status, self._now(), suggestion_id),
        )
        self.conn.commit()

    # === Stats ===

    def get_stats(self) -> dict:
        """Summary stats for corp status command."""
        total = self.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        by_status = self.conn.execute(
            """SELECT status, COUNT(*) AS cnt
               FROM assets GROUP BY status ORDER BY cnt DESC""",
        ).fetchall()
        by_folder = self.conn.execute(
            """SELECT folder_l1, COUNT(*) AS cnt
               FROM assets GROUP BY folder_l1 ORDER BY cnt DESC""",
        ).fetchall()
        by_ext = self.conn.execute(
            """SELECT extension, COUNT(*) AS cnt
               FROM assets GROUP BY extension ORDER BY cnt DESC LIMIT 10""",
        ).fetchall()
        total_events = self.conn.execute(
            "SELECT COUNT(*) FROM ingest_events",
        ).fetchone()[0]
        total_packages = self.conn.execute(
            "SELECT COUNT(*) FROM packages",
        ).fetchone()[0]
        pending_suggestions = self.conn.execute(
            "SELECT COUNT(*) FROM registry_suggestions WHERE status = 'pending'",
        ).fetchone()[0]

        return {
            "total_assets": total,
            "by_status": {row["status"]: row["cnt"] for row in by_status},
            "by_folder": {row["folder_l1"]: row["cnt"] for row in by_folder},
            "top_extensions": {row["extension"]: row["cnt"] for row in by_ext},
            "total_events": total_events,
            "total_packages": total_packages,
            "pending_suggestions": pending_suggestions,
        }
