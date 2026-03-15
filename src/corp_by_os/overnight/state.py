"""Overnight run state tracking in SQLite.

Idempotent, crash-safe. Resumes after power loss or crash by
re-reading pending files from the database.

DB location: {app_data_path}/overnight_state.db
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    scope TEXT NOT NULL,
    budget_limit REAL DEFAULT 1.0,
    model TEXT,
    status TEXT DEFAULT 'running',
    total_files INTEGER DEFAULT 0,
    processed_files INTEGER DEFAULT 0,
    failed_files INTEGER DEFAULT 0,
    skipped_files INTEGER DEFAULT 0,
    actual_cost REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    path TEXT NOT NULL,
    file_hash TEXT,
    status TEXT DEFAULT 'pending',
    batch_id TEXT,
    tier TEXT,
    retry_count INTEGER DEFAULT 0,
    error TEXT,
    processed_at TEXT,
    cost REAL DEFAULT 0.0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    submitted_at TEXT,
    completed_at TEXT,
    file_count INTEGER DEFAULT 0,
    chunk_index INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_files_status ON files(run_id, status);
CREATE INDEX IF NOT EXISTS idx_files_batch ON files(batch_id);
"""


def get_state_db_path() -> Path:
    """Default DB path from config."""
    from corp_by_os.config import get_config

    return get_config().app_data_path / "overnight_state.db"


class OvernightState:
    """Manages overnight run state in SQLite."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_state_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # --- Runs ---

    def create_run(
        self,
        run_id: str,
        scope: str,
        budget: float = 1.0,
        model: str | None = None,
    ) -> None:
        """Create a new run record."""
        self.conn.execute(
            """INSERT INTO runs (run_id, started_at, scope, budget_limit, model)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, datetime.now().isoformat(timespec="seconds"), scope, budget, model),
        )
        self.conn.commit()
        logger.info("Created run %s (scope=%s, budget=$%.2f)", run_id, scope, budget)

    def complete_run(self, run_id: str, status: str = "completed") -> None:
        """Mark a run as completed."""
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "UPDATE runs SET completed_at = ?, status = ? WHERE run_id = ?",
            (now, status, run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict | None:
        """Get run record as dict."""
        row = self.conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    # --- Files ---

    def add_file(
        self,
        run_id: str,
        path: str,
        file_hash: str,
        tier: str = "unknown",
    ) -> int:
        """Register a file for processing. Returns file ID."""
        cur = self.conn.execute(
            """INSERT INTO files (run_id, path, file_hash, tier)
               VALUES (?, ?, ?, ?)""",
            (run_id, path, file_hash, tier),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def update_file_status(
        self,
        file_id: int,
        status: str,
        *,
        batch_id: str | None = None,
        error: str | None = None,
        cost: float | None = None,
    ) -> None:
        """Update a file's processing status."""
        parts = ["status = ?"]
        params: list = [status]

        if status in ("done", "error"):
            parts.append("processed_at = ?")
            params.append(datetime.now().isoformat(timespec="seconds"))
        if batch_id is not None:
            parts.append("batch_id = ?")
            params.append(batch_id)
        if error is not None:
            parts.append("error = ?")
            params.append(error)
        if cost is not None:
            parts.append("cost = ?")
            params.append(cost)

        params.append(file_id)
        sql = f"UPDATE files SET {', '.join(parts)} WHERE id = ?"
        self.conn.execute(sql, params)
        self.conn.commit()

    def increment_retry(self, file_id: int) -> None:
        """Bump retry counter for a file."""
        self.conn.execute(
            "UPDATE files SET retry_count = retry_count + 1 WHERE id = ?",
            (file_id,),
        )
        self.conn.commit()

    def get_pending_files(self, run_id: str) -> list[dict]:
        """Get all files still pending for a run."""
        rows = self.conn.execute(
            "SELECT * FROM files WHERE run_id = ? AND status = 'pending'",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_failed_files(self, run_id: str) -> list[dict]:
        """Get all failed files for a run."""
        rows = self.conn.execute(
            "SELECT * FROM files WHERE run_id = ? AND status = 'error'",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Batches ---

    def create_batch(
        self,
        batch_id: str,
        run_id: str,
        file_ids: list[int],
        chunk_index: int = 0,
    ) -> None:
        """Register a batch job."""
        self.conn.execute(
            """INSERT INTO batches (batch_id, run_id, submitted_at, file_count, chunk_index)
               VALUES (?, ?, ?, ?, ?)""",
            (
                batch_id,
                run_id,
                datetime.now().isoformat(timespec="seconds"),
                len(file_ids),
                chunk_index,
            ),
        )
        for fid in file_ids:
            self.update_file_status(fid, "submitted", batch_id=batch_id)
        self.conn.commit()

    def update_batch_status(self, batch_id: str, status: str) -> None:
        """Update batch job status."""
        parts = ["status = ?"]
        params: list = [status]
        if status in ("completed", "failed"):
            parts.append("completed_at = ?")
            params.append(datetime.now().isoformat(timespec="seconds"))
        params.append(batch_id)
        sql = f"UPDATE batches SET {', '.join(parts)} WHERE batch_id = ?"
        self.conn.execute(sql, params)
        self.conn.commit()

    # --- Aggregates ---

    def get_run_stats(self, run_id: str) -> dict:
        """Aggregate stats for a run."""
        run = self.get_run(run_id)
        if not run:
            return {}

        counts = self.conn.execute(
            """SELECT
                 COUNT(*) AS total,
                 SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
                 SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
                 SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped,
                 SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                 COALESCE(SUM(cost), 0.0) AS total_cost
               FROM files WHERE run_id = ?""",
            (run_id,),
        ).fetchone()

        return {
            "run_id": run_id,
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "status": run["status"],
            "scope": run["scope"],
            "budget_limit": run["budget_limit"],
            "total_files": counts["total"],
            "processed_files": counts["done"],
            "failed_files": counts["errors"],
            "skipped_files": counts["skipped"],
            "pending_files": counts["pending"],
            "actual_cost": counts["total_cost"],
        }

    def get_cumulative_cost(self, run_id: str) -> float:
        """Sum of all file costs for a run."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost), 0.0) FROM files WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return float(row[0])

    def sync_run_counters(self, run_id: str) -> None:
        """Sync run-level counters from file-level data."""
        stats = self.get_run_stats(run_id)
        if not stats:
            return
        self.conn.execute(
            """UPDATE runs SET
                 total_files = ?, processed_files = ?,
                 failed_files = ?, skipped_files = ?,
                 actual_cost = ?
               WHERE run_id = ?""",
            (
                stats["total_files"],
                stats["processed_files"],
                stats["failed_files"],
                stats["skipped_files"],
                stats["actual_cost"],
                run_id,
            ),
        )
        self.conn.commit()
