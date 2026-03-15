"""Generate moves.yaml from classification results.

Produces a human-reviewable YAML file with proposed moves.
Human sets approved: true/false, then runs corp apply-moves.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .classifier import Classification

log = logging.getLogger(__name__)


def _build_move_entry(c: Classification) -> dict[str, Any]:
    """Build a single entry for moves.yaml."""
    return {
        "source": c.file_info.relative_path,
        "action": c.action,
        "destination": c.destination_folder,
        "proposed_name": c.proposed_name,
        "reason": c.reason,
        "confidence": round(c.confidence, 2),
        "approved": None,  # human fills this in
    }


def generate_proposals(
    classifications: list[Classification],
    output_path: Path,
) -> Path:
    """Write moves.yaml with all proposed actions.

    Format is designed for human review: each entry has an 'approved'
    field that must be set to true before execution.
    """
    entries = [_build_move_entry(c) for c in classifications]

    # Sort: high confidence first, then by source path
    entries.sort(key=lambda e: (-e["confidence"], e["source"]))

    data: dict[str, Any] = {
        "version": "1.0",
        "description": (
            "Proposed file moves for MyWork cleanup. Review and set approved: true/false."
        ),
        "summary": {
            "total": len(entries),
            "moves": sum(1 for e in entries if e["action"] == "move"),
            "deletes": sum(1 for e in entries if e["action"] == "delete"),
            "keeps": sum(1 for e in entries if e["action"] == "keep"),
        },
        "moves": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    log.info("Wrote %d proposals to %s", len(entries), output_path)
    return output_path
