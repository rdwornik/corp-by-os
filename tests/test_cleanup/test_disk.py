"""Tests for disk cleanup — overlap, duplicates, artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corp_by_os.cleanup.disk import (
    CleanupItem,
    CleanupPlan,
    execute_plan,
    find_duplicates,
    find_extraction_artifacts,
    find_onedrive_overlap,
    find_staging_artifacts,
)


# === OneDrive overlap ===


class TestFindOverlap:
    def test_finds_matching_files(self, tmp_path: Path) -> None:
        """Finds files matching name+size in both locations."""
        local = tmp_path / "local"
        onedrive = tmp_path / "onedrive"
        local.mkdir()
        onedrive.mkdir()

        content = b"x" * 1024
        (local / "report.pdf").write_bytes(content)
        (onedrive / "report.pdf").write_bytes(content)

        plan = find_onedrive_overlap(local, onedrive)
        assert plan.total_files == 1
        assert plan.items[0].filename == "report.pdf"
        assert plan.items[0].category == "overlap"
        assert str(onedrive) in plan.items[0].path

    def test_no_overlap_different_size(self, tmp_path: Path) -> None:
        """Files with same name but different size are not overlap."""
        local = tmp_path / "local"
        onedrive = tmp_path / "onedrive"
        local.mkdir()
        onedrive.mkdir()

        (local / "doc.pdf").write_bytes(b"short")
        (onedrive / "doc.pdf").write_bytes(b"much longer content")

        plan = find_onedrive_overlap(local, onedrive)
        assert plan.total_files == 0

    def test_no_overlap_different_name(self, tmp_path: Path) -> None:
        """Files with different names are not overlap."""
        local = tmp_path / "local"
        onedrive = tmp_path / "onedrive"
        local.mkdir()
        onedrive.mkdir()

        content = b"same content"
        (local / "local_file.pdf").write_bytes(content)
        (onedrive / "cloud_file.pdf").write_bytes(content)

        plan = find_onedrive_overlap(local, onedrive)
        assert plan.total_files == 0

    def test_nested_files(self, tmp_path: Path) -> None:
        """Finds overlap in nested subdirectories."""
        local = tmp_path / "local" / "Projects" / "Client_A"
        onedrive = tmp_path / "onedrive" / "Old" / "Client_A"
        local.mkdir(parents=True)
        onedrive.mkdir(parents=True)

        content = b"y" * 2048
        (local / "demo.pptx").write_bytes(content)
        (onedrive / "demo.pptx").write_bytes(content)

        plan = find_onedrive_overlap(
            tmp_path / "local",
            tmp_path / "onedrive",
        )
        assert plan.total_files == 1

    def test_nonexistent_onedrive(self, tmp_path: Path) -> None:
        """Missing OneDrive path returns empty plan."""
        local = tmp_path / "local"
        local.mkdir()
        plan = find_onedrive_overlap(local, tmp_path / "nonexistent")
        assert plan.total_files == 0

    def test_zero_size_files_ignored(self, tmp_path: Path) -> None:
        """Zero-size files are not counted as overlap."""
        local = tmp_path / "local"
        onedrive = tmp_path / "onedrive"
        local.mkdir()
        onedrive.mkdir()

        (local / "empty.txt").write_bytes(b"")
        (onedrive / "empty.txt").write_bytes(b"")

        plan = find_onedrive_overlap(local, onedrive)
        assert plan.total_files == 0

    def test_keep_path_is_local(self, tmp_path: Path) -> None:
        """Keep path points to the local copy."""
        local = tmp_path / "local"
        onedrive = tmp_path / "onedrive"
        local.mkdir()
        onedrive.mkdir()

        content = b"content"
        (local / "file.pdf").write_bytes(content)
        (onedrive / "file.pdf").write_bytes(content)

        plan = find_onedrive_overlap(local, onedrive)
        assert plan.items[0].keep_path is not None
        assert "local" in plan.items[0].keep_path


# === Duplicate detection ===


class TestFindDuplicates:
    def test_detects_duplicates(self, tmp_path: Path) -> None:
        """Detects duplicate files by name+size."""
        dir_a = tmp_path / "A"
        dir_b = tmp_path / "B"
        dir_a.mkdir()
        dir_b.mkdir()

        content = b"duplicate content"
        (dir_a / "file.pdf").write_bytes(content)
        (dir_b / "file.pdf").write_bytes(content)

        plan = find_duplicates(tmp_path)
        assert plan.total_files == 1
        assert plan.items[0].category == "duplicate"

    def test_keeps_shortest_path(self, tmp_path: Path) -> None:
        """Keeps the copy with the shortest path."""
        short = tmp_path / "A"
        long = tmp_path / "B" / "C" / "D"
        short.mkdir()
        long.mkdir(parents=True)

        content = b"content"
        (short / "file.pdf").write_bytes(content)
        (long / "file.pdf").write_bytes(content)

        plan = find_duplicates(tmp_path)
        assert plan.total_files == 1
        # The kept path should be the shorter one
        assert "A" in plan.items[0].keep_path

    def test_no_duplicates(self, tmp_path: Path) -> None:
        """Unique files produce empty plan."""
        (tmp_path / "a.pdf").write_bytes(b"alpha")
        (tmp_path / "b.pdf").write_bytes(b"beta")

        plan = find_duplicates(tmp_path)
        assert plan.total_files == 0

    def test_three_copies(self, tmp_path: Path) -> None:
        """Three copies of same file produces two deletion items."""
        for name in ("d1", "d2", "d3"):
            d = tmp_path / name
            d.mkdir()
            (d / "same.txt").write_bytes(b"triplicate")

        plan = find_duplicates(tmp_path)
        assert plan.total_files == 2


# === Extraction artifacts ===


class TestFindArtifacts:
    def test_finds_extraction_artifacts(self, tmp_path: Path) -> None:
        """Finds files in .corp/run/ directory."""
        run_dir = tmp_path / "90_System" / ".corp" / "run" / "batch_001" / "output"
        run_dir.mkdir(parents=True)
        (run_dir / "result.json").write_bytes(b"result")
        (run_dir / "source.pdf").write_bytes(b"x" * 5000)

        plan = find_extraction_artifacts(tmp_path)
        assert plan.total_files == 2
        assert all(i.category == "artifact" for i in plan.items)

    def test_no_run_dir(self, tmp_path: Path) -> None:
        """Missing .corp/run/ returns empty plan."""
        plan = find_extraction_artifacts(tmp_path)
        assert plan.total_files == 0


class TestFindStagingArtifacts:
    def test_finds_staging(self, tmp_path: Path) -> None:
        """Finds files in staging directory."""
        staging = tmp_path / "staging" / "ingest" / "entry_1"
        staging.mkdir(parents=True)
        (staging / "manifest.json").write_bytes(b"{}")

        plan = find_staging_artifacts(tmp_path)
        assert plan.total_files == 1

    def test_no_staging_dir(self, tmp_path: Path) -> None:
        """Missing staging dir returns empty plan."""
        plan = find_staging_artifacts(tmp_path)
        assert plan.total_files == 0


# === Execution ===


class TestExecutePlan:
    def test_dry_run_no_delete(self, tmp_path: Path) -> None:
        """Dry run counts size without deleting."""
        target = tmp_path / "to_delete.txt"
        target.write_bytes(b"content")

        plan = CleanupPlan()
        plan.add(
            CleanupItem(
                path=str(target),
                filename="to_delete.txt",
                size_bytes=7,
                category="artifact",
                reason="test",
            )
        )

        log_path = tmp_path / "log.jsonl"
        deleted, failed = execute_plan(plan, log_path, dry_run=True)

        assert deleted == 0
        assert failed == 0
        assert target.exists()  # NOT deleted
        assert not log_path.exists()  # No log written

    def test_execute_deletes_files(self, tmp_path: Path) -> None:
        """Execute mode removes files and logs."""
        target = tmp_path / "to_delete.txt"
        target.write_bytes(b"delete me")

        plan = CleanupPlan()
        plan.add(
            CleanupItem(
                path=str(target),
                filename="to_delete.txt",
                size_bytes=9,
                category="overlap",
                reason="test deletion",
            )
        )

        log_path = tmp_path / "cleanup_log.jsonl"
        deleted, failed = execute_plan(plan, log_path, dry_run=False)

        assert deleted == 1
        assert failed == 0
        assert not target.exists()  # Deleted

        # Check log
        assert log_path.exists()
        record = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert record["action"] == "deleted"
        assert record["filename"] == "to_delete.txt"
        assert record["category"] == "overlap"

    def test_execute_missing_file(self, tmp_path: Path) -> None:
        """Missing file counts as failed, not crashed."""
        plan = CleanupPlan()
        plan.add(
            CleanupItem(
                path=str(tmp_path / "ghost.txt"),
                filename="ghost.txt",
                size_bytes=100,
                category="artifact",
                reason="test",
            )
        )

        log_path = tmp_path / "log.jsonl"
        deleted, failed = execute_plan(plan, log_path, dry_run=False)

        assert deleted == 0
        assert failed == 1

    def test_log_multiple_deletions(self, tmp_path: Path) -> None:
        """Multiple deletions appended to same log file."""
        plan = CleanupPlan()
        for i in range(3):
            f = tmp_path / f"file_{i}.txt"
            f.write_bytes(b"x")
            plan.add(
                CleanupItem(
                    path=str(f),
                    filename=f.name,
                    size_bytes=1,
                    category="duplicate",
                    reason="test",
                )
            )

        log_path = tmp_path / "log.jsonl"
        deleted, failed = execute_plan(plan, log_path, dry_run=False)

        assert deleted == 3
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_cleans_empty_dirs(self, tmp_path: Path) -> None:
        """Empty directories are removed after deletion."""
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        target = sub / "only_file.txt"
        target.write_bytes(b"x")

        plan = CleanupPlan()
        plan.add(
            CleanupItem(
                path=str(target),
                filename="only_file.txt",
                size_bytes=1,
                category="artifact",
                reason="test",
            )
        )

        log_path = tmp_path / "log.jsonl"
        execute_plan(plan, log_path, dry_run=False)

        assert not sub.exists()  # Empty dir removed


# === CleanupPlan ===


class TestCleanupPlan:
    def test_totals(self) -> None:
        """Plan tracks total bytes and files."""
        plan = CleanupPlan()
        plan.add(
            CleanupItem(
                path="/a.txt",
                filename="a.txt",
                size_bytes=1024,
                category="test",
                reason="test",
            )
        )
        plan.add(
            CleanupItem(
                path="/b.txt",
                filename="b.txt",
                size_bytes=2048,
                category="test",
                reason="test",
            )
        )

        assert plan.total_files == 2
        assert plan.total_bytes == 3072
        assert plan.total_mb == 0.0  # rounds to 0 at this scale

    def test_total_gb(self) -> None:
        """GB calculation works."""
        plan = CleanupPlan()
        plan.add(
            CleanupItem(
                path="/big.bin",
                filename="big.bin",
                size_bytes=2 * 1024**3,  # 2 GB
                category="test",
                reason="test",
            )
        )
        assert plan.total_gb == 2.0
