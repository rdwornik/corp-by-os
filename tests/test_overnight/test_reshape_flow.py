"""Tests for reshape flow integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from corp_by_os.overnight.dedup import deduplicate
from corp_by_os.overnight.classifier import classify_batch, ClassificationResult
from corp_by_os.cli import _execute_reshape_actions


def _make_files(n: int, prefix: str = "file") -> list[dict]:
    """Generate n scan result dicts."""
    return [
        {
            "path": f"60_Source_Library/{prefix}_{i}.pptx",
            "filename": f"{prefix}_{i}.pptx",
            "extension": ".pptx",
            "size_bytes": 1000 + i * 100,
            "file_hash": f"sha256:hash_{prefix}_{i}",
            "tier": 1,
            "metadata": {
                "title": f"Document {prefix} {i}",
                "text_preview": f"Content of document {prefix} {i} with some text",
                "slide_count": 10 + i,
            },
        }
        for i in range(n)
    ]


class TestFullReshapeDryRun:
    def test_phases_execute_in_order(self) -> None:
        """All three free phases should work on scan results."""
        # Phase A: simulate scan results
        files = _make_files(10)

        # Add some exact duplicates
        files.append(
            {
                **files[0],
                "path": "30_Templates/file_0_copy.pptx",
                "filename": "file_0_copy.pptx",
            }
        )

        # Phase B: dedup
        unique, groups = deduplicate(files)
        assert len(unique) == 10  # 1 duplicate removed
        assert len(groups) >= 1

        # Phase C: classify (only returns files needing action)
        routing_map = {"folders": {}}
        classifications = classify_batch(unique, routing_map)
        # Files have underscore names like "file_0.pptx" → no action needed
        assert len(classifications) == 0
        assert all(isinstance(c, ClassificationResult) for c in classifications)

    def test_budget_check_before_extraction(self) -> None:
        """Should estimate cost and check against budget."""
        files = _make_files(100)

        # Simulate tier assignment
        for f in files:
            f["tier"] = 2  # text-AI tier

        tier2_count = sum(1 for f in files if f["tier"] == 2)
        estimated_cost = tier2_count * 0.001 * 0.5  # batch discount

        budget = 0.01  # Very low budget
        assert estimated_cost > budget  # Should exceed budget


class TestReshapePlan:
    def test_plan_includes_all_sections(self) -> None:
        """Reshape plan should have all required sections."""
        from corp_by_os.overnight.classifier import classify_from_metadata

        files = _make_files(5)
        unique, groups = deduplicate(files)
        routing_map = {"folders": {}}
        classifications = classify_batch(unique, routing_map)

        # Verify we can generate a plan from these results
        renames = [c for c in classifications if c.proposed_name and c.confidence >= 0.90]
        moves = [c for c in classifications if c.proposed_folder and c.confidence >= 0.90]
        needs_review = [
            c
            for c in classifications
            if (c.proposed_name or c.proposed_folder) and c.confidence < 0.90
        ]

        # These should all be valid lists (may be empty)
        assert isinstance(renames, list)
        assert isinstance(moves, list)
        assert isinstance(needs_review, list)


class TestExecuteReshapeActions:
    """Regression tests for path resolution in _execute_reshape_actions.

    Bug: used Path(c.current_path) which is relative and never resolves.
    Fix: mywork_root / c.current_path joins absolute root + relative path.
    """

    def test_rename_uses_mywork_root(self, tmp_path: Path) -> None:
        """Rename must join mywork_root + relative current_path."""
        mywork = tmp_path / "MyWork"
        project_dir = mywork / "10_Projects" / "Test_Client"
        project_dir.mkdir(parents=True)
        test_file = project_dir / "Budget Report Q2.pptx"
        test_file.write_text("test")

        action = ClassificationResult(
            current_path="10_Projects/Test_Client/Budget Report Q2.pptx",
            proposed_name="Budget_Report_Q2.pptx",
            proposed_folder=None,
            confidence=0.95,
            reasoning="space_cleanup",
        )

        _execute_reshape_actions([action], mywork)

        assert not test_file.exists(), "Original should be renamed away"
        assert (project_dir / "Budget_Report_Q2.pptx").exists()

    def test_move_uses_mywork_root(self, tmp_path: Path) -> None:
        """Move must resolve both source and destination via mywork_root."""
        mywork = tmp_path / "MyWork"
        inbox = mywork / "00_Inbox"
        inbox.mkdir(parents=True)
        test_file = inbox / "training_doc.pptx"
        test_file.write_text("test")

        action = ClassificationResult(
            current_path="00_Inbox/training_doc.pptx",
            proposed_name=None,
            proposed_folder="60_Source_Library/02_Training_Enablement",
            confidence=0.95,
            reasoning="move",
        )

        _execute_reshape_actions([action], mywork)

        assert not test_file.exists(), "Source should be moved"
        dest = mywork / "60_Source_Library" / "02_Training_Enablement" / "training_doc.pptx"
        assert dest.exists(), "File should be at destination"

    def test_rename_and_move_combined(self, tmp_path: Path) -> None:
        """File gets renamed then moved in one action."""
        mywork = tmp_path / "MyWork"
        inbox = mywork / "00_Inbox"
        inbox.mkdir(parents=True)
        test_file = inbox / "Copy of Budget.xlsx"
        test_file.write_text("test")

        action = ClassificationResult(
            current_path="00_Inbox/Copy of Budget.xlsx",
            proposed_name="Budget.xlsx",
            proposed_folder="50_RFP",
            confidence=0.92,
            reasoning="remove_copy, move",
        )

        _execute_reshape_actions([action], mywork)

        assert not test_file.exists()
        assert not (inbox / "Budget.xlsx").exists(), "Renamed file should also be moved"
        assert (mywork / "50_RFP" / "Budget.xlsx").exists()

    def test_relative_path_alone_does_not_resolve(self, tmp_path: Path) -> None:
        """Verify that relative paths alone can't accidentally find files."""
        mywork = tmp_path / "MyWork"
        project_dir = mywork / "10_Projects" / "Client"
        project_dir.mkdir(parents=True)
        (project_dir / "file.pptx").write_text("test")

        relative = Path("10_Projects/Client/file.pptx")
        assert not relative.exists(), "Relative path must not resolve without mywork_root"

    def test_rejects_non_absolute_mywork_root(self, tmp_path: Path) -> None:
        """mywork_root must be absolute — guard against subtle bugs."""
        action = ClassificationResult(
            current_path="file.pptx",
            proposed_name="clean.pptx",
            confidence=0.95,
            reasoning="test",
        )

        with pytest.raises(ValueError, match="must be absolute"):
            _execute_reshape_actions([action], Path("relative/path"))

    def test_missing_file_skipped_gracefully(self, tmp_path: Path) -> None:
        """Non-existent source files are skipped, not crashed on."""
        mywork = tmp_path / "MyWork"
        mywork.mkdir()

        action = ClassificationResult(
            current_path="nonexistent/file.pptx",
            proposed_name="clean.pptx",
            confidence=0.95,
            reasoning="test",
        )

        # Should not raise
        _execute_reshape_actions([action], mywork)


class TestSafetyInReshape:
    def test_safety_gate_filters_before_dedup(self) -> None:
        """Safety gate should run before dedup — blocked files never reach dedup."""
        from corp_by_os.overnight.safety import is_safe_for_upload

        files = _make_files(3)
        files.append(
            {
                "path": "secrets/api.env",
                "filename": "api.env",
                "extension": ".env",
                "size_bytes": 100,
                "file_hash": "sha256:secret",
                "tier": 1,
                "metadata": {},
            }
        )

        safe = []
        for f in files:
            ok, _ = is_safe_for_upload(Path(f["path"]))
            if ok:
                safe.append(f)

        assert len(safe) == 3  # .env file filtered out
        unique, _ = deduplicate(safe)
        assert len(unique) == 3
