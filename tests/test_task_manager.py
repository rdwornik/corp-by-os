"""Tests for task_manager module."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from corp_by_os.config import get_config
from corp_by_os.models import TaskPriority, TaskStatus
from corp_by_os.task_manager import (
    _parse_task_file,
    _slugify_title,
    _tasks_root,
    add_task,
    complete_task,
    list_tasks,
    task_dashboard_md,
)


# --- Fixtures ---


@pytest.fixture()
def task_env(app_config, tmp_vault: Path):
    """Set up task environment with vault."""
    tasks_dir = tmp_vault / "00_dashboards" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return tasks_dir


@pytest.fixture()
def populated_tasks(task_env: Path) -> Path:
    """Create a few task files for testing."""
    tasks = [
        {
            "title": "Prepare Siemens deck",
            "status": "todo",
            "priority": "high",
            "project": "siemens_wms",
            "deadline": "2026-03-12",
            "created": "2026-03-08",
            "completed": None,
            "tags": ["task"],
        },
        {
            "title": "Review Lenzing RFP",
            "status": "todo",
            "priority": "medium",
            "project": "lenzing_planning",
            "deadline": "2026-03-10",
            "created": "2026-03-07",
            "completed": None,
            "tags": ["task"],
        },
        {
            "title": "Update PepsiCo notes",
            "status": "in_progress",
            "priority": "low",
            "created": "2026-03-06",
            "completed": None,
            "tags": ["task"],
        },
        {
            "title": "Old completed task",
            "status": "done",
            "priority": "medium",
            "created": "2026-03-01",
            "completed": "2026-03-05",
            "tags": ["task"],
        },
    ]

    for i, fm in enumerate(tasks):
        slug = fm["title"].lower().replace(" ", "-")[:40]
        filename = f"2026-03-0{i + 1}_{slug}.md"
        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        content = f"---\n{fm_str}\n---\n\nNotes here.\n"
        (task_env / filename).write_text(content, encoding="utf-8")

    return task_env


# --- Test: Slugify ---


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify_title("Prepare Siemens deck") == "prepare-siemens-deck"

    def test_special_chars(self) -> None:
        assert _slugify_title("Fix bug #123 (urgent!)") == "fix-bug-123-urgent"

    def test_truncates_long_titles(self) -> None:
        long_title = "A" * 100
        assert len(_slugify_title(long_title)) <= 60


# --- Test: Add Task ---


class TestAddTask:
    def test_creates_file(self, task_env: Path, app_config) -> None:
        path = add_task(title="Test task", priority="high")
        assert path.exists()
        assert path.suffix == ".md"
        assert "test-task" in path.name

    def test_frontmatter_fields(self, task_env: Path, app_config) -> None:
        path = add_task(
            title="My task",
            project_id="lenzing_planning",
            deadline="2026-03-15",
            priority="high",
        )
        content = path.read_text(encoding="utf-8")
        assert "title: My task" in content
        assert "priority: high" in content
        assert "project: lenzing_planning" in content
        assert "deadline: '2026-03-15'" in content or "deadline: 2026-03-15" in content
        assert "status: todo" in content

    def test_duplicate_filename_handled(self, task_env: Path, app_config) -> None:
        p1 = add_task(title="Same title")
        p2 = add_task(title="Same title")
        assert p1 != p2
        assert p1.exists()
        assert p2.exists()

    def test_with_project_only(self, task_env: Path, app_config) -> None:
        path = add_task(title="Task with project", project_id="honda_wms")
        content = path.read_text(encoding="utf-8")
        assert "project: honda_wms" in content


# --- Test: List Tasks ---


class TestListTasks:
    def test_list_todo(self, populated_tasks: Path, app_config) -> None:
        tasks = list_tasks(status_filter="todo")
        assert len(tasks) == 2
        assert all(t.status == TaskStatus.TODO for t in tasks)

    def test_list_all(self, populated_tasks: Path, app_config) -> None:
        tasks = list_tasks(status_filter=None)
        assert len(tasks) == 4

    def test_filter_by_project(self, populated_tasks: Path, app_config) -> None:
        tasks = list_tasks(status_filter=None, project_filter="siemens_wms")
        assert len(tasks) == 1
        assert tasks[0].title == "Prepare Siemens deck"

    def test_sort_by_priority(self, populated_tasks: Path, app_config) -> None:
        tasks = list_tasks(status_filter="todo", sort_by="priority")
        assert tasks[0].priority == TaskPriority.HIGH
        assert tasks[1].priority == TaskPriority.MEDIUM

    def test_sort_by_deadline(self, populated_tasks: Path, app_config) -> None:
        tasks = list_tasks(status_filter="todo", sort_by="deadline")
        # Earlier deadline first
        assert tasks[0].deadline <= tasks[1].deadline

    def test_empty_vault(self, task_env: Path, app_config) -> None:
        tasks = list_tasks()
        assert tasks == []

    def test_in_progress_filter(self, populated_tasks: Path, app_config) -> None:
        tasks = list_tasks(status_filter="in_progress")
        assert len(tasks) == 1
        assert tasks[0].title == "Update PepsiCo notes"


# --- Test: Complete Task ---


class TestCompleteTask:
    def test_complete_by_title(self, populated_tasks: Path, app_config) -> None:
        result = complete_task("Siemens")
        assert result is True

        # Verify it's actually done
        tasks = list_tasks(status_filter="done")
        titles = [t.title for t in tasks]
        assert "Prepare Siemens deck" in titles

    def test_complete_by_filename(self, populated_tasks: Path, app_config) -> None:
        result = complete_task("lenzing")
        assert result is True

    def test_complete_nonexistent(self, populated_tasks: Path, app_config) -> None:
        result = complete_task("nonexistent task xyz")
        assert result is False

    def test_completed_date_set(self, populated_tasks: Path, app_config) -> None:
        complete_task("Siemens")
        tasks = list_tasks(status_filter="done")
        siemens = [t for t in tasks if "Siemens" in t.title]
        assert len(siemens) == 1
        assert siemens[0].completed == date.today().isoformat()


# --- Test: Parse Task File ---


class TestParseTaskFile:
    def test_valid_file(self, populated_tasks: Path) -> None:
        files = list(populated_tasks.glob("*.md"))
        task = _parse_task_file(files[0])
        assert task is not None
        assert task.title

    def test_invalid_file(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.md"
        bad_file.write_text("No frontmatter here", encoding="utf-8")
        assert _parse_task_file(bad_file) is None

    def test_missing_title(self, tmp_path: Path) -> None:
        file = tmp_path / "notitle.md"
        file.write_text("---\nstatus: todo\n---\nBody\n", encoding="utf-8")
        assert _parse_task_file(file) is None


# --- Test: Dashboard ---


class TestDashboard:
    def test_dashboard_md(self) -> None:
        md = task_dashboard_md()
        assert "Tasks Dashboard" in md
        assert "dataview" in md
        assert "00_dashboards/tasks" in md
