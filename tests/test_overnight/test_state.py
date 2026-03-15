"""Tests for overnight state tracking."""

from __future__ import annotations

from pathlib import Path

import pytest

from corp_by_os.overnight.state import OvernightState


@pytest.fixture()
def state(tmp_path: Path) -> OvernightState:
    db = tmp_path / "test_state.db"
    return OvernightState(db_path=db)


class TestRuns:
    def test_create_run(self, state: OvernightState) -> None:
        state.create_run("run_001", "templates", budget=2.0, model="flash")
        run = state.get_run("run_001")
        assert run is not None
        assert run["scope"] == "templates"
        assert run["budget_limit"] == 2.0
        assert run["model"] == "flash"
        assert run["status"] == "running"

    def test_complete_run(self, state: OvernightState) -> None:
        state.create_run("run_002", "all-non-project")
        state.complete_run("run_002", status="completed")
        run = state.get_run("run_002")
        assert run["status"] == "completed"
        assert run["completed_at"] is not None

    def test_get_nonexistent_run(self, state: OvernightState) -> None:
        assert state.get_run("nope") is None


class TestFiles:
    def test_add_and_update_files(self, state: OvernightState) -> None:
        state.create_run("r1", "templates")
        fid = state.add_file("r1", "/path/to/file.pptx", "abc123", "tier2")
        assert isinstance(fid, int)

        state.update_file_status(fid, "done", cost=0.001)
        stats = state.get_run_stats("r1")
        assert stats["processed_files"] == 1
        assert stats["actual_cost"] == pytest.approx(0.001)

    def test_get_pending_files(self, state: OvernightState) -> None:
        state.create_run("r2", "rfp")
        state.add_file("r2", "/a.pdf", "h1", "tier2")
        state.add_file("r2", "/b.pdf", "h2", "tier2")
        fid3 = state.add_file("r2", "/c.pdf", "h3", "tier2")

        state.update_file_status(fid3, "done")
        pending = state.get_pending_files("r2")
        assert len(pending) == 2

    def test_cumulative_cost(self, state: OvernightState) -> None:
        state.create_run("r3", "source-library")
        f1 = state.add_file("r3", "/x.docx", "ha", "tier2")
        f2 = state.add_file("r3", "/y.docx", "hb", "tier3")

        state.update_file_status(f1, "done", cost=0.001)
        state.update_file_status(f2, "done", cost=0.03)

        cost = state.get_cumulative_cost("r3")
        assert cost == pytest.approx(0.031)

    def test_failed_files(self, state: OvernightState) -> None:
        state.create_run("r4", "templates")
        f1 = state.add_file("r4", "/bad.pptx", "h1", "tier2")
        state.update_file_status(f1, "error", error="Gemini timeout")

        failed = state.get_failed_files("r4")
        assert len(failed) == 1
        assert failed[0]["error"] == "Gemini timeout"

    def test_increment_retry(self, state: OvernightState) -> None:
        state.create_run("r5", "templates")
        fid = state.add_file("r5", "/retry.pdf", "h1", "tier2")
        state.increment_retry(fid)
        state.increment_retry(fid)

        row = state.conn.execute(
            "SELECT retry_count FROM files WHERE id = ?",
            (fid,),
        ).fetchone()
        assert row["retry_count"] == 2


class TestBatches:
    def test_create_batch(self, state: OvernightState) -> None:
        state.create_run("r6", "templates")
        f1 = state.add_file("r6", "/a.pdf", "h1", "tier2")
        f2 = state.add_file("r6", "/b.pdf", "h2", "tier2")

        state.create_batch("batch_001", "r6", [f1, f2], chunk_index=0)

        row = state.conn.execute(
            "SELECT * FROM batches WHERE batch_id = 'batch_001'",
        ).fetchone()
        assert row["file_count"] == 2
        assert row["chunk_index"] == 0

        # Files should now be "submitted"
        pending = state.get_pending_files("r6")
        assert len(pending) == 0


class TestIdempotentResume:
    def test_idempotent_resume(self, state: OvernightState) -> None:
        """Simulate crash and resume: pending files should still be there."""
        state.create_run("crash_run", "rfp", budget=5.0)
        f1 = state.add_file("crash_run", "/a.pdf", "h1", "tier2")
        f2 = state.add_file("crash_run", "/b.pdf", "h2", "tier2")
        f3 = state.add_file("crash_run", "/c.pdf", "h3", "tier2")

        # Simulate partial progress before "crash"
        state.update_file_status(f1, "done", cost=0.001)

        # "Resume" — re-open same DB
        state2 = OvernightState(db_path=state.db_path)
        pending = state2.get_pending_files("crash_run")
        assert len(pending) == 2  # f2 and f3 still pending

        run = state2.get_run("crash_run")
        assert run["status"] == "running"
        state2.close()


class TestRunStats:
    def test_get_run_stats(self, state: OvernightState) -> None:
        state.create_run("stats_run", "all-non-project", budget=3.0)
        f1 = state.add_file("stats_run", "/a.pdf", "h1", "tier2")
        f2 = state.add_file("stats_run", "/b.pdf", "h2", "tier2")
        f3 = state.add_file("stats_run", "/c.pdf", "h3", "tier2")

        state.update_file_status(f1, "done", cost=0.01)
        state.update_file_status(f2, "error", error="fail")

        stats = state.get_run_stats("stats_run")
        assert stats["total_files"] == 3
        assert stats["processed_files"] == 1
        assert stats["failed_files"] == 1
        assert stats["pending_files"] == 1
        assert stats["actual_cost"] == pytest.approx(0.01)

    def test_sync_run_counters(self, state: OvernightState) -> None:
        state.create_run("sync_run", "templates")
        f1 = state.add_file("sync_run", "/a.pdf", "h1", "tier2")
        state.update_file_status(f1, "done", cost=0.005)

        state.sync_run_counters("sync_run")
        run = state.get_run("sync_run")
        assert run["processed_files"] == 1
        assert run["actual_cost"] == pytest.approx(0.005)
