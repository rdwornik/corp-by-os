"""File-based monitoring — no terminal needed.

Writes progress to JSON/JSONL/Markdown files in .corp/ so the user
can check status anytime without a running terminal.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from corp_by_os.overnight.state import OvernightState

logger = logging.getLogger(__name__)


def _get_monitor_dir() -> Path:
    """Resolve monitor directory from config."""
    from corp_by_os.config import get_config

    return get_config().mywork_root / "90_System" / ".corp"


class OvernightMonitor:
    """Writes progress to files that user can check anytime."""

    def __init__(self, run_id: str, monitor_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.base_dir = monitor_dir or _get_monitor_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.status_path = self.base_dir / "overnight_status.json"
        self.log_path = self.base_dir / f"overnight_log_{run_id}.jsonl"
        self.report_path = self.base_dir / f"overnight_report_{run_id}.md"

    def heartbeat(self, stats: dict) -> None:
        """Write current status — called periodically during run."""
        status = {
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "status": "running",
            **stats,
        }
        self.status_path.write_text(
            json.dumps(status, indent=2),
            encoding="utf-8",
        )
        logger.debug("Heartbeat written to %s", self.status_path)

    def log_event(self, event_type: str, **data: object) -> None:
        """Append structured event to JSONL log."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event_type,
            **data,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def mark_complete(self, status: str = "completed") -> None:
        """Update status file to final state."""
        current = {}
        if self.status_path.exists():
            try:
                current = json.loads(self.status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        current.update(
            {
                "status": status,
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.status_path.write_text(
            json.dumps(current, indent=2),
            encoding="utf-8",
        )

    def write_morning_report(self, state: OvernightState) -> Path:
        """Generate markdown summary for morning review."""
        stats = state.get_run_stats(self.run_id)
        if not stats:
            self.report_path.write_text(
                f"# Overnight Report — {self.run_id}\n\nNo data found.\n",
                encoding="utf-8",
            )
            return self.report_path

        cost = stats.get("actual_cost", 0.0)
        budget = stats.get("budget_limit", 0.0)

        lines = [
            f"# Overnight Report — {self.run_id}",
            "",
            f"**Started:** {stats['started_at']}",
            f"**Completed:** {stats.get('completed_at') or 'in progress'}",
            f"**Status:** {stats['status']}",
            f"**Scope:** {stats['scope']}",
            "",
            "## Summary",
            f"- Total files: {stats['total_files']}",
            f"- Processed: {stats['processed_files']}",
            f"- Failed: {stats['failed_files']}",
            f"- Skipped: {stats['skipped_files']}",
            f"- Pending: {stats.get('pending_files', 0)}",
            f"- Cost: ${cost:.4f} / ${budget:.2f} budget",
            "",
        ]

        # Add errors section
        failed = state.get_failed_files(self.run_id)
        if failed:
            lines.append("## Errors")
            lines.append("")
            for f in failed[:20]:  # Cap at 20 to keep report readable
                lines.append(f"- **{f['path']}**: {f.get('error', 'unknown')}")
            if len(failed) > 20:
                lines.append(f"- ... and {len(failed) - 20} more")
            lines.append("")
        else:
            lines.append("## Errors")
            lines.append("")
            lines.append("No errors.")
            lines.append("")

        # Add action items
        lines.append("## Next Steps")
        lines.append("")
        if failed:
            lines.append(f"- [ ] Review {len(failed)} failed files")
        if stats.get("pending_files", 0) > 0:
            lines.append(
                f"- [ ] {stats['pending_files']} files still pending — "
                "re-run `corp overnight` to resume"
            )
        if not failed and stats.get("pending_files", 0) == 0:
            lines.append("- All files processed successfully.")
        lines.append("")

        self.report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Morning report written to %s", self.report_path)
        return self.report_path
