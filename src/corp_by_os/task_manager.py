"""Task management via Obsidian vault notes.

Tasks are markdown files in 00_dashboards/tasks/ with YAML frontmatter.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import yaml

from corp_by_os.config import get_config
from corp_by_os.models import Task, TaskPriority, TaskStatus, VaultZone

logger = logging.getLogger(__name__)

TASKS_DIR = "tasks"


def _tasks_root() -> Path:
    """Get the tasks directory in the vault."""
    cfg = get_config()
    return cfg.vault_path / VaultZone.DASHBOARDS.value / TASKS_DIR


def _slugify_title(title: str) -> str:
    """Convert a title to an English filename-safe slug.

    Strips Polish stop words and common verbs, keeps meaningful
    content words (project names, English loanwords, nouns).
    """
    # Polish stop words / filler verbs that add no meaning to a filename
    _PL_STOP = {
        # prepositions / conjunctions
        "na",
        "do",
        "w",
        "z",
        "ze",
        "od",
        "po",
        "dla",
        "o",
        "i",
        "a",
        "nie",
        "co",
        "jak",
        "to",
        "sie",
        "ale",
        "czy",
        "lub",
        # common task verbs (infinitive + imperative forms)
        "przygotowac",
        "przygotuj",
        "zrobic",
        "zrob",
        "wyslac",
        "wyslij",
        "sprawdzic",
        "sprawdz",
        "napisac",
        "napisz",
        "umiescic",
        "umiesc",
        "zaplanowac",
        "zaplanuj",
        "przypomn",
        "przypomnic",
        "przypomnij",
        "przejrzec",
        "przejrzyj",
        "zaktualizowac",
        "zaktualizuj",
        "dodac",
        "dodaj",
        "stworzyc",
        "stworz",
        "wyciagnac",
        "wyciagnij",
        "przetworzyc",
        "przetwarz",
        "archiwizuj",
        "archiwizowac",
        "musze",
        "trzeba",
        "nalezy",
        "powinien",
        # English filler
        "the",
        "an",
        "for",
        "of",
        "and",
        "or",
        "in",
        "on",
        "need",
        "must",
        "should",
        "prepare",
        "review",
        "send",
        "check",
        "create",
        "make",
        "update",
        "add",
        "write",
    }
    # Strip diacritics for comparison
    from corp_by_os.intent_router import _strip_diacritics

    normalized = _strip_diacritics(title.lower().strip())
    words = re.split(r"[^a-z0-9]+", normalized)
    content = [w for w in words if w and w not in _PL_STOP and len(w) > 1]

    if not content:
        # Fallback: use all words
        content = [w for w in words if w]

    slug = "-".join(content)
    return slug[:60] if slug else "task"


def add_task(
    title: str,
    project_id: str | None = None,
    deadline: str | None = None,
    priority: str = "medium",
) -> Path:
    """Create a task note in the vault.

    Returns:
        Path to the created task file.
    """
    tasks_dir = _tasks_root()
    tasks_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    slug = _slugify_title(title)
    filename = f"{today}_{slug}.md"
    task_path = tasks_dir / filename

    # Handle duplicate filenames
    counter = 1
    while task_path.exists():
        counter += 1
        filename = f"{today}_{slug}_{counter}.md"
        task_path = tasks_dir / filename

    frontmatter = {
        "title": title,
        "status": "todo",
        "priority": priority,
        "created": today,
        "source_tool": "corp-by-os",
        "tags": ["task"],
    }
    if project_id:
        frontmatter["project"] = project_id
    if deadline:
        frontmatter["deadline"] = deadline

    fm_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True).strip()
    content = f"---\n{fm_str}\n---\n\nAdd notes here as needed.\n"

    task_path.write_text(content, encoding="utf-8")
    logger.info("Created task: %s", task_path)
    return task_path


def list_tasks(
    status_filter: str | None = "todo",
    project_filter: str | None = None,
    sort_by: str = "priority",
) -> list[Task]:
    """List tasks from vault, optionally filtered.

    Args:
        status_filter: Filter by status (todo, in_progress, done, cancelled). None = all.
        project_filter: Filter by project_id. None = all.
        sort_by: Sort key: "priority", "deadline", "created".

    Returns:
        Sorted list of Task objects.
    """
    tasks_dir = _tasks_root()
    if not tasks_dir.exists():
        return []

    tasks: list[Task] = []
    for task_file in tasks_dir.glob("*.md"):
        task = _parse_task_file(task_file)
        if task is None:
            continue

        # Apply filters
        if status_filter and task.status.value != status_filter:
            continue
        if project_filter and task.project != project_filter:
            continue

        tasks.append(task)

    # Sort
    priority_order = {"high": 0, "medium": 1, "low": 2}
    if sort_by == "priority":
        tasks.sort(key=lambda t: (priority_order.get(t.priority.value, 9), t.deadline or "9999"))
    elif sort_by == "deadline":
        tasks.sort(key=lambda t: (t.deadline or "9999", priority_order.get(t.priority.value, 9)))
    else:
        tasks.sort(key=lambda t: t.created, reverse=True)

    return tasks


def complete_task(title_or_filename: str) -> bool:
    """Mark a task as done by title substring or filename.

    Returns:
        True if a task was found and completed.
    """
    tasks_dir = _tasks_root()
    if not tasks_dir.exists():
        return False

    query = title_or_filename.lower()

    for task_file in tasks_dir.glob("*.md"):
        task = _parse_task_file(task_file)
        if task is None:
            continue

        # Match by title substring or filename
        if query in task.title.lower() or query in task_file.name.lower():
            return _update_task_status(task_file, TaskStatus.DONE)

    return False


def task_dashboard_md() -> str:
    """Generate markdown for the tasks dashboard with Dataview query."""
    lines = [
        "---",
        "title: Tasks Dashboard",
        "document_type: dashboard",
        f'generated: "{date.today().isoformat()}"',
        "tags: [dashboard, auto-generated]",
        "---",
        "",
        "# Tasks",
        "",
        "```dataview",
        "TABLE priority, deadline, project, status",
        'FROM "00_dashboards/tasks"',
        'WHERE status = "todo" OR status = "in_progress"',
        "SORT priority ASC, deadline ASC",
        "```",
    ]
    return "\n".join(lines)


# --- Internals ---


def _parse_task_file(path: Path) -> Task | None:
    """Parse a task markdown file into a Task object."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    if not content.startswith("---"):
        return None

    try:
        end_idx = content.index("\n---", 3)
        fm_text = content[4:end_idx]
        fm = yaml.safe_load(fm_text)
    except (ValueError, yaml.YAMLError):
        return None

    if not isinstance(fm, dict) or "title" not in fm:
        return None

    try:
        status = TaskStatus(fm.get("status", "todo"))
    except ValueError:
        status = TaskStatus.TODO

    try:
        priority = TaskPriority(fm.get("priority", "medium"))
    except ValueError:
        priority = TaskPriority.MEDIUM

    return Task(
        title=fm["title"],
        status=status,
        project=fm.get("project"),
        deadline=str(fm["deadline"]) if fm.get("deadline") else None,
        priority=priority,
        created=str(fm.get("created", "")),
        completed=str(fm["completed"]) if fm.get("completed") else None,
        file_path=path,
    )


def _update_task_status(path: Path, new_status: TaskStatus) -> bool:
    """Update the status field in a task file's frontmatter."""
    try:
        content = path.read_text(encoding="utf-8")
        end_idx = content.index("\n---", 3)
        fm_text = content[4:end_idx]
        body = content[end_idx + 4 :]

        fm = yaml.safe_load(fm_text) or {}
        fm["status"] = new_status.value

        if new_status == TaskStatus.DONE:
            fm["completed"] = date.today().isoformat()

        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        new_content = f"---\n{fm_str}\n---{body}"
        path.write_text(new_content, encoding="utf-8")
        logger.info("Updated task status to %s: %s", new_status.value, path)
        return True
    except Exception as e:
        logger.error("Failed to update task: %s", e)
        return False
