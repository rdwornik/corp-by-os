"""Data models for corp-by-os.

Dataclasses (not Pydantic) — lightweight, typed, frozen where appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class VaultZone(str, Enum):
    """Obsidian vault top-level folders."""

    DASHBOARDS = "00_dashboards"
    PROJECTS = "01_projects"
    SOURCES = "02_sources"
    PLAYBOOKS = "03_playbooks"
    EVERGREEN = "04_evergreen"
    TEMPLATES = "05_templates"


class Mutability(str, Enum):
    """Folder mutability rules per INTEGRATION_SPEC."""

    IMMUTABLE = "immutable"
    REGENERABLE = "regenerable"
    PROTECTED = "protected"
    APPEND_ONLY = "append_only"


ZONE_MUTABILITY: dict[VaultZone, Mutability] = {
    VaultZone.DASHBOARDS: Mutability.REGENERABLE,
    VaultZone.PROJECTS: Mutability.REGENERABLE,
    VaultZone.SOURCES: Mutability.IMMUTABLE,
    VaultZone.PLAYBOOKS: Mutability.PROTECTED,
    VaultZone.EVERGREEN: Mutability.REGENERABLE,
    VaultZone.TEMPLATES: Mutability.PROTECTED,
}


@dataclass(frozen=True)
class VaultPath:
    """Resolved path within the Obsidian vault."""

    zone: VaultZone
    project_id: str | None
    filename: str | None
    absolute: Path


@dataclass
class ProjectInfo:
    """Mirrors project-info.yaml schema."""

    project_id: str
    client: str
    status: str  # active | rfp | proposal | won | lost | archived
    products: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    files_processed: int = 0
    facts_count: int = 0
    last_extracted: str | None = None
    # Optional fields
    people: list[str] = field(default_factory=list)
    stage: str | None = None
    opportunity_id: str | None = None
    region: str | None = None
    industry: str | None = None


@dataclass
class ProjectSummary:
    """Lightweight project overview for list displays."""

    project_id: str
    client: str
    status: str
    has_vault: bool
    has_onedrive: bool
    facts_count: int = 0
    onedrive_path: Path | None = None
    vault_path: Path | None = None


@dataclass(frozen=True)
class ResolvedProject:
    """Result of fuzzy project resolution."""

    project_id: str
    folder_name: str  # original folder name (mixed case)
    onedrive_path: Path | None
    vault_path: Path | None
    score: float  # match quality 0.0-1.0


@dataclass
class ValidationIssue:
    """Single validation problem."""

    path: Path
    level: str  # error | warning
    message: str


@dataclass
class ValidationReport:
    """Result of vault validation."""

    project_id: str | None
    issues: list[ValidationIssue] = field(default_factory=list)
    notes_checked: int = 0
    notes_valid: int = 0

    @property
    def is_valid(self) -> bool:
        return not any(i.level == "error" for i in self.issues)
