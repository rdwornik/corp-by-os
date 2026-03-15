"""Tests for overnight file-based monitoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corp_by_os.overnight.monitor import OvernightMonitor
from corp_by_os.overnight.state import OvernightState


@pytest.fixture()
def monitor(tmp_path: Path) -> OvernightMonitor:
    return OvernightMonitor("test_run_001", monitor_dir=tmp_path)


@pytest.fixture()
def state(tmp_path: Path) -> OvernightState:
    return OvernightState(db_path=tmp_path / "state.db")


class TestHeartbeat:
    def test_heartbeat_writes_json(self, monitor: OvernightMonitor) -> None:
        monitor.heartbeat({"total_files": 10, "processed_files": 3})

        assert monitor.status_path.exists()
        data = json.loads(monitor.status_path.read_text(encoding="utf-8"))
        assert data["run_id"] == "test_run_001"
        assert data["status"] == "running"
        assert data["total_files"] == 10
        assert data["processed_files"] == 3
        assert "timestamp" in data

    def test_heartbeat_overwrites(self, monitor: OvernightMonitor) -> None:
        monitor.heartbeat({"processed_files": 1})
        monitor.heartbeat({"processed_files": 5})

        data = json.loads(monitor.status_path.read_text(encoding="utf-8"))
        assert data["processed_files"] == 5


class TestLogEvent:
    def test_log_event_appends_jsonl(self, monitor: OvernightMonitor) -> None:
        monitor.log_event("file_done", path="/a.pdf", cost=0.001)
        monitor.log_event("file_done", path="/b.pdf", cost=0.002)

        lines = monitor.log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        first = json.loads(lines[0])
        assert first["event"] == "file_done"
        assert first["path"] == "/a.pdf"
        assert "timestamp" in first


class TestMarkComplete:
    def test_mark_complete(self, monitor: OvernightMonitor) -> None:
        monitor.heartbeat({"total_files": 10})
        monitor.mark_complete("completed")

        data = json.loads(monitor.status_path.read_text(encoding="utf-8"))
        assert data["status"] == "completed"
        assert "completed_at" in data


class TestMorningReport:
    def test_morning_report_markdown(
        self,
        monitor: OvernightMonitor,
        state: OvernightState,
    ) -> None:
        state.create_run("test_run_001", "templates", budget=2.0)
        f1 = state.add_file("test_run_001", "/a.pptx", "h1", "tier2")
        f2 = state.add_file("test_run_001", "/b.pptx", "h2", "tier2")
        state.update_file_status(f1, "done", cost=0.01)
        state.update_file_status(f2, "error", error="timeout")
        state.complete_run("test_run_001")

        path = monitor.write_morning_report(state)
        assert path.exists()

        content = path.read_text(encoding="utf-8")
        assert "# Overnight Report" in content
        assert "Total files: 2" in content
        assert "Processed: 1" in content
        assert "Failed: 1" in content
        assert "timeout" in content

    def test_morning_report_no_data(self, monitor: OvernightMonitor, state: OvernightState) -> None:
        path = monitor.write_morning_report(state)
        assert "No data found" in path.read_text(encoding="utf-8")
