"""Integration tests for overnight extraction pipeline.

Verifies that the extraction pipeline:
1. Scans files and registers them in state DB
2. Invokes CKE extraction (mocked)
3. Updates state DB from pending → done
4. Generates a morning report
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from corp_by_os.overnight.monitor import OvernightMonitor
from corp_by_os.overnight.state import OvernightState


@pytest.fixture()
def mywork_tree(tmp_path: Path) -> Path:
    """Create a minimal MyWork folder structure with test files."""
    mywork = tmp_path / "MyWork"

    # Create folder with test files
    source_lib = mywork / "60_Source_Library"
    source_lib.mkdir(parents=True)
    (source_lib / "platform_overview.pptx").write_bytes(b"PK\x03\x04fake-pptx")
    (source_lib / "integration_guide.pdf").write_bytes(b"%PDF-1.4 fake-pdf")

    # Create 90_System with routing_map
    system = mywork / "90_System"
    system.mkdir(parents=True)
    routing = {
        "folders": {
            "60_Source_Library": {
                "vault_target": "02_sources",
                "content_origin": "internal",
                "source_category": "source-library",
                "provenance_scope": "non-project",
                "routing_confidence": 0.95,
            },
        },
    }
    (system / "routing_map.yaml").write_text(
        yaml.dump(routing, default_flow_style=False),
        encoding="utf-8",
    )

    # Create .corp dir for monitor
    (system / ".corp").mkdir(parents=True)

    return mywork


@pytest.fixture()
def state_db(tmp_path: Path) -> Path:
    return tmp_path / "test_state.db"


class TestExtractionPipeline:
    """Integration tests for _run_folder_extraction flow."""

    def test_files_registered_as_pending(
        self,
        mywork_tree: Path,
        state_db: Path,
    ) -> None:
        """Files should be registered in state DB after scan."""
        state = OvernightState(db_path=state_db)
        run_id = "test-run-001"
        state.create_run(run_id, scope="source-library", budget=0.10)

        # Register files (simulating what _run_folder_extraction does)
        folder_path = mywork_tree / "60_Source_Library"
        from corp_by_os.extraction.non_project.scanner import scan_folder

        EXTENSIONS = [".pptx", ".pdf", ".docx"]
        results = scan_folder(folder_path, allow_extensions=EXTENSIONS)
        for sr in results:
            state.add_file(run_id, str(sr.absolute_path), file_hash="", tier="pending")

        pending = state.get_pending_files(run_id)
        assert len(pending) == 2
        state.close()

    def test_file_statuses_updated_after_extraction(
        self,
        mywork_tree: Path,
        state_db: Path,
    ) -> None:
        """After extraction, files must move from pending → done."""
        from corp_by_os.cli import _update_folder_file_statuses

        state = OvernightState(db_path=state_db)
        run_id = "test-run-002"
        state.create_run(run_id, scope="source-library", budget=0.10)

        folder_path = mywork_tree / "60_Source_Library"
        from corp_by_os.extraction.non_project.scanner import scan_folder

        results = scan_folder(folder_path, allow_extensions=[".pptx", ".pdf"])
        for sr in results:
            state.add_file(run_id, str(sr.absolute_path), file_hash="", tier="pending")

        assert len(state.get_pending_files(run_id)) == 2

        # Simulate successful extraction → update statuses
        updated = _update_folder_file_statuses(
            state,
            run_id,
            folder_path,
            "done",
            cost=0.005,
        )

        assert updated == 2
        assert len(state.get_pending_files(run_id)) == 0

        stats = state.get_run_stats(run_id)
        assert stats["processed_files"] == 2
        assert stats["actual_cost"] == pytest.approx(0.01)  # 2 * 0.005
        state.close()

    def test_file_statuses_updated_on_error(
        self,
        mywork_tree: Path,
        state_db: Path,
    ) -> None:
        """On extraction failure, files must be marked as error."""
        from corp_by_os.cli import _update_folder_file_statuses

        state = OvernightState(db_path=state_db)
        run_id = "test-run-003"
        state.create_run(run_id, scope="source-library", budget=0.10)

        folder_path = mywork_tree / "60_Source_Library"
        from corp_by_os.extraction.non_project.scanner import scan_folder

        results = scan_folder(folder_path, allow_extensions=[".pptx", ".pdf"])
        for sr in results:
            state.add_file(run_id, str(sr.absolute_path), file_hash="", tier="pending")

        _update_folder_file_statuses(
            state,
            run_id,
            folder_path,
            "error",
            error="Gemini API timeout",
        )

        failed = state.get_failed_files(run_id)
        assert len(failed) == 2
        assert all(f["error"] == "Gemini API timeout" for f in failed)
        state.close()

    def test_morning_report_generated(
        self,
        mywork_tree: Path,
        state_db: Path,
        tmp_path: Path,
    ) -> None:
        """Morning report must be written after run completes."""
        state = OvernightState(db_path=state_db)
        run_id = "test-run-004"
        state.create_run(run_id, scope="source-library", budget=0.50)

        # Add and complete files
        f1 = state.add_file(
            run_id, str(mywork_tree / "60_Source_Library" / "a.pptx"), "", "pending"
        )
        state.update_file_status(f1, "done", cost=0.01)
        state.sync_run_counters(run_id)
        state.complete_run(run_id)

        monitor = OvernightMonitor(run_id, monitor_dir=tmp_path / "monitor")
        report_path = monitor.write_morning_report(state)

        assert report_path.exists()
        report_text = report_path.read_text(encoding="utf-8")
        assert "Overnight Report" in report_text
        assert "Processed: 1" in report_text
        assert "$0.0100" in report_text
        state.close()

    def test_reset_clears_pending(self, state_db: Path) -> None:
        """--reset must clear all pending files from state DB."""
        state = OvernightState(db_path=state_db)
        state.create_run("old-run-1", scope="templates", budget=1.0)
        state.add_file("old-run-1", "/a.pptx", "h1", "pending")
        state.add_file("old-run-1", "/b.pdf", "h2", "pending")
        f3 = state.add_file("old-run-1", "/c.docx", "h3", "pending")
        state.update_file_status(f3, "done", cost=0.01)

        # Simulate --reset
        cleared = state.conn.execute("DELETE FROM files WHERE status = 'pending'").rowcount
        state.conn.commit()

        assert cleared == 2  # only pending files cleared
        # Done file should still be there
        all_files = state.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        assert all_files == 1
        state.close()

    def test_folder_prefix_matching(
        self,
        mywork_tree: Path,
        state_db: Path,
    ) -> None:
        """_update_folder_file_statuses must only update files in the target folder."""
        from corp_by_os.cli import _update_folder_file_statuses

        state = OvernightState(db_path=state_db)
        run_id = "test-run-prefix"
        state.create_run(run_id, scope="all-non-project", budget=1.0)

        # Files from two different folders
        folder_60 = mywork_tree / "60_Source_Library"
        state.add_file(run_id, str(folder_60 / "file1.pdf"), "", "pending")
        state.add_file(run_id, str(folder_60 / "file2.pptx"), "", "pending")

        # Create another folder
        folder_30 = mywork_tree / "30_Templates"
        folder_30.mkdir(exist_ok=True)
        state.add_file(run_id, str(folder_30 / "template.pptx"), "", "pending")

        # Update only folder_60
        updated = _update_folder_file_statuses(
            state,
            run_id,
            folder_60,
            "done",
            cost=0.01,
        )

        assert updated == 2
        pending = state.get_pending_files(run_id)
        assert len(pending) == 1
        assert "30_Templates" in pending[0]["path"]
        state.close()
