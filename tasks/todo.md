# Task: Bootstrap corp-by-os — Step 0 + Step 1

## Overview

Corp-by-os is the root orchestrator of the agent ecosystem. It's a **thin, deterministic CLI workflow runner** that routes natural language to agents, executes multi-step workflows, and manages the Obsidian vault as a data bus. It contains NO domain logic — extraction, RFP, opportunity management all live in their respective agents.

**Decision source:** AI Council debate (4 models, 2 rounds, 12 consensus points). Architecture: "Deterministic CLI Orchestrator with Vault Bus."

**What exists and works:**
- corp-os-meta v2.0 (39 tests) — shared schema, taxonomy, validation
- corp-knowledge-extractor (114+ tests) — tiered file → note extraction  
- corp-project-extractor (39 tests) — scan + extract + render pipeline
- corp-opportunity-manager (61 tests) — opportunity lifecycle + chat
- corp-rfp-agent (v0.1 legacy) — RFP answer engine
- ai-council — multi-model debate tool
- corp-pdf-toolkit — PDF→MD + anonymization
- Obsidian vault — 123 Lenzing notes, 5 dashboards, Sync active

**Proven pipeline (Lenzing):**
```
cpe scan (228 files) → cpe extract-cke (141/143, $0.20) → cpe render (992 facts) → Obsidian (123 notes, 5 dashboards)
```

---

## Step 0: INTEGRATION_SPEC.md

### Create `INTEGRATION_SPEC.md` in repo root. This is THE contract all agents follow.

```markdown
# Integration Specification — Corp-by-os Agent Ecosystem

## 1. Obsidian Vault Structure

Path: `C:\Users\1028120\Documents\ObsidianVault`
Sync: Obsidian Sync Standard ($4/mo, vault: corp-brain)
Git backup: corporate GitHub (TBD)

### Folder Mutability Rules

| Folder | Purpose | Mutability | Writer |
|---|---|---|---|
| 00_dashboards/ | Dataview queries, status overviews, generated reports | REGENERABLE | corp-by-os (attention_scan workflow) |
| 01_projects/ | Per-client: project-info.yaml, index.md, facts.yaml | REGENERABLE | cpe render, corp-by-os vault_io |
| 02_sources/ | Immutable extracted knowledge notes | IMMUTABLE (append-only) | cke process, cpe extract-cke |
| 03_playbooks/ | Curated RFP answers, demo scripts | PROTECTED (human-curated) | corp-rfp-agent, human |
| 04_evergreen/ | Synthesized "current truth" per topic | REGENERABLE | future synthesis pipeline |
| 05_templates/ | Note templates, presentation templates index | PROTECTED | human, com prep-deck |
| _assets/ | Images, attachments | APPEND-ONLY | cke (slide frames) |

### Rules
- IMMUTABLE folders: once written, never overwritten. New versions get new filenames.
- REGENERABLE folders: can be fully rebuilt from source data. Safe to delete and re-run.
- PROTECTED folders: human content. Agents may ADD but never DELETE or OVERWRITE existing files.

## 2. Project Identity Standard

Every project MUST have a stable `project_id` in its `project-info.yaml`.

Format: `{client_slug}_{product_slug}`
Examples: `lenzing_planning`, `honda_wms`, `stellantis_e2e`

Rules:
- Lowercase, underscores, no spaces
- Matches folder name in `10_Projects/` (OneDrive) and `01_projects/` (Obsidian)
- Once assigned, NEVER changes (even if client renames opportunity)
- `project-info.yaml` is the canonical source of project metadata

### project-info.yaml Required Fields (what cpe render produces)
```yaml
project_id: lenzing_planning       # REQUIRED, stable slug
client: Lenzing AG                 # REQUIRED, display name
status: active                     # REQUIRED: active | rfp | proposal | won | lost | archived
products: [Blue Yonder Demand Planning]  # top products
topics: [Supply Chain Planning, Demand Planning]  # top topics by frequency
domains: [Product, Delivery & Implementation]  # top knowledge domains
files_processed: 141               # extraction stats
facts_count: 992
last_extracted: "2026-03-06"
```

### project-info.yaml Optional Fields
```yaml
people: [name (role)]              # key contacts
stage: discovery | rfp | proposal | negotiation | won | lost | archived
opportunity_id: "OP-12345"        # Salesforce reference
region: EMEA | NA | APAC
industry: manufacturing | retail | pharma | logistics
```

## 3. Agent Registry

Each agent is invoked via CLI subprocess. NEVER import agent code directly.

| Agent | CLI | Install | Key Commands |
|---|---|---|---|
| corp-os-meta | corp-meta | pip install -e | validate, normalize, report |
| corp-knowledge-extractor | cke | pip install -e | process, process-manifest |
| corp-project-extractor | cpe | pip install -e | scan, extract-cke, render |
| corp-opportunity-manager | com | pip install -e | new, list, show, prep-deck, chat |
| corp-rfp-agent | (TBD) | — | (legacy, needs rewire) |
| ai-council | council | python -m src.cli | (question as arg or file) |
| corp-pdf-toolkit | pdf2md | python pdf2md.py | (file as arg) |

### Invocation Rules
- Always `subprocess.run(shell=False)` with explicit executable
- Capture stdout + stderr, log both
- For payloads >4KB: write JSON to `%LOCALAPPDATA%\corp-by-os\jobs\job_<uuid>.json`, pass path
- Validate all arguments via Pydantic before calling subprocess
- Never pass raw LLM output to subprocess

## 4. Naming Conventions

### Files
| Type | Pattern | Example |
|---|---|---|
| Knowledge note | `{type}-{slug}.md` | `presentation-lenzing-discovery.md` |
| Project info | `project-info.yaml` | (always this exact name) |
| Facts file | `facts.yaml` | (always this exact name) |
| Project index | `index.md` | (always this exact name) |
| Dashboard | `{name}.md` | `active-opportunities.md` |
| Presentation | `{Client}_{Date}_{Topic}.pptx` | `Lenzing_2026-03-15_Discovery.pptx` |
| Meeting folder | `{YYYY.MM.DD} - {topic}/` | `2025.07.24 - meeting/` |

### Obsidian Note Paths
| Zone | Path Pattern |
|---|---|
| Project overview | `01_projects/{project_id}/index.md` |
| Project info | `01_projects/{project_id}/project-info.yaml` |
| Project facts | `01_projects/{project_id}/facts.yaml` |
| Source note | `02_sources/{project_id}/{note_filename}.md` |
| Playbook | `03_playbooks/{domain}/{topic}.md` |

## 5. Workflow Definitions

### Workflow: new_opportunity
```yaml
id: new_opportunity
description: "Create new opportunity — folder, deck, metadata, vault skeleton"
trigger_phrases: ["nowe opportunity", "new opportunity", "nowy klient", "new client"]
parameters:
  client: {type: string, required: true}
  product: {type: string, required: true}
  contact: {type: string, required: false}
steps:
  - agent: corp-opportunity-manager
    command: ["com", "new", "{client}", "-p", "{product}"]
    condition_args:
      contact: ["-c", "{contact}"]
  - action: create_vault_skeleton
    description: "Create 01_projects/{project_id}/ in Obsidian vault"
  - action: validate
    description: "Run corp-os-meta validation on new project-info.yaml"
confirmation: true
```

### Workflow: extract_project
```yaml
id: extract_project
description: "Full extraction pipeline — scan, extract, render, copy to Obsidian"
trigger_phrases: ["przetwórz projekt", "extract project", "wyciągnij wiedzę"]
parameters:
  project_path: {type: path, required: true}
steps:
  - agent: corp-project-extractor
    command: ["cpe", "scan", "{project_path}"]
  - agent: corp-project-extractor
    command: ["cpe", "extract-cke", "{project_path}", "--max-rpm", "50"]
  - agent: corp-project-extractor
    command: ["cpe", "render", "{project_path}"]
  - action: copy_to_vault
    source: "{project_path}/_knowledge/"
    target: "01_projects/{project_id}/"
  - action: copy_notes_to_vault
    source: "{project_path}/_extracted/notes/"
    target: "02_sources/{project_id}/"
  - action: validate
confirmation: true
cost_estimate: "$0.20 per project (tiered extraction)"
```

### Workflow: attention_scan
```yaml
id: attention_scan
description: "Scan all projects for items needing attention"
trigger_phrases: ["co wymaga uwagi", "what needs attention", "status", "przegląd"]
parameters: {}
steps:
  - action: scan_all_projects
    description: "Read all project-info.yaml files, check for stale/missing/overdue"
  - action: generate_dashboard
    output: "00_dashboards/attention.md"
    checks:
      - stale: "last_extracted > 14 days ago"
      - missing_metadata: "project-info.yaml missing or incomplete"
      - no_extraction: "files_processed == 0"
confirmation: false
```
```

---

## Step 1: Vault IO + Project Resolver

### 1.1 Repository Structure

```
corp-by-os/
├── CLAUDE.md
├── README.md
├── INTEGRATION_SPEC.md             # THE contract (from Step 0)
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── agents.yaml                 # Agent registry (Step 2, stub for now)
│   └── workflows.yaml              # Workflow definitions (Step 2, stub for now)
├── src/
│   └── corp_by_os/
│       ├── __init__.py
│       ├── cli.py                  # Click CLI, entry point: "corp"
│       ├── vault_io.py             # Read/write Obsidian vault (THE core module)
│       ├── project_resolver.py     # Name/code → path resolution
│       ├── models.py               # Dataclasses: ProjectInfo, ProjectSummary, VaultPath
│       └── config.py               # Settings from .env + YAML
├── tests/
│   ├── conftest.py
│   ├── test_vault_io.py
│   ├── test_project_resolver.py
│   └── fixtures/                   # Minimal vault structure for testing
└── tasks/
    ├── todo.md                     # This file
    └── lessons.md
```

### 1.2 pyproject.toml

```toml
[project]
name = "corp-by-os"
version = "0.1.0"
description = "Root orchestrator for the corp agent ecosystem"
requires-python = ">=3.10"
dependencies = [
    "click>=8.0",
    "rich>=13.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "corp-os-meta",
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov", "ruff"]

[project.scripts]
corp = "corp_by_os.cli:cli"
```

### 1.3 .env.example

```bash
# Obsidian vault (local, NOT OneDrive)
VAULT_PATH=C:\Users\1028120\Documents\ObsidianVault

# OneDrive project folders
PROJECTS_ROOT=C:\Users\1028120\OneDrive - Blue Yonder\MyWork\10_Projects
ARCHIVE_ROOT=C:\Users\1028120\OneDrive - Blue Yonder\MyWork\80_Archive

# Local app data (SQLite index, job files — NOT synced)
APP_DATA_PATH=%LOCALAPPDATA%\corp-by-os

# Gemini (for Step 3 chat router)
# GEMINI_API_KEY=
```

### 1.4 vault_io.py — Core Module

```python
"""Vault IO — the single writer to Obsidian vault.

All agents write to vault THROUGH this module (via corp-by-os workflows).
Direct agent writes to vault are allowed for now but should migrate here.

Key responsibilities:
- Resolve project paths (OneDrive ↔ Vault)
- Read/write notes with frontmatter validation (via corp-os-meta)
- Idempotent writes (check stable ID before creating)
- File-lock retry with exponential backoff (OneDrive sync conflicts)
- Path normalization for Windows
"""
```

Functions needed:
- `resolve_vault_path(zone: str, project_id: str, filename: str) -> Path`
- `write_note(path: Path, frontmatter: dict, body: str, mode: str) -> bool` — mode: create|update|upsert
- `read_note(path: Path) -> tuple[dict, str]` — returns (frontmatter, body)
- `read_project_info(project_id: str) -> ProjectInfo | None`
- `list_projects(filter: dict | None) -> list[ProjectSummary]`
- `copy_to_vault(source: Path, zone: str, project_id: str) -> list[Path]`
- `validate_vault(path: Path | None) -> ValidationReport`

File-lock retry:
```python
def _write_with_retry(path: Path, content: str, max_retries: int = 5):
    """Write with exponential backoff for OneDrive locks."""
    for attempt in range(max_retries):
        try:
            path.write_text(content, encoding="utf-8")
            return True
        except (PermissionError, OSError) as e:
            if attempt < max_retries - 1:
                wait = 0.1 * (2 ** attempt)  # 100ms, 200ms, 400ms, 800ms, 1600ms
                time.sleep(wait)
            else:
                raise
```

### 1.5 project_resolver.py

```python
"""Resolve project references to concrete paths.

Handles: "Lenzing" → project_id "lenzing_planning" → paths in OneDrive + Vault
"""
```

Functions needed:
- `resolve_project(name_or_id: str) -> ResolvedProject` — fuzzy match client name to project folder
- `get_onedrive_path(project_id: str) -> Path`
- `get_vault_path(project_id: str) -> Path`
- `list_all_project_ids() -> list[str]`

Fuzzy matching: lowercase, strip underscores, find best match in `10_Projects/` folders. No LLM needed.

### 1.6 CLI Commands (Step 1)

```
corp project list                    List all projects with metadata status
corp project show <name>             Show project details (from project-info.yaml)
corp project open <name>             Open project folder in Explorer
corp vault validate                  Run corp-os-meta validation across vault
corp vault validate <project>        Validate single project
corp doctor                          Check all agent CLIs are on PATH and working
```

### 1.7 Tests

- `test_vault_io.py` — write/read roundtrip, upsert idempotency, file-lock retry (mock), path normalization
- `test_project_resolver.py` — fuzzy matching ("Lenzing" → "lenzing_planning"), case insensitivity, list all
- Test against Lenzing pilot data structure

---

## Implementation Checklist

### Step 0
- [x] Create repo at `C:\Users\1028120\Documents\Scripts\corp-by-os`
- [x] git init, feature/bootstrap branch
- [x] Write INTEGRATION_SPEC.md
- [x] CLAUDE.md (copy standard)
- [x] Initial commit

### Step 1
- [x] pyproject.toml, .env.example, .gitignore
- [x] models.py — ProjectInfo, ProjectSummary, ResolvedProject, ValidationReport
- [x] config.py — load .env + YAML
- [x] vault_io.py — read/write/validate/copy with retry logic
- [x] project_resolver.py — fuzzy matching, path resolution
- [x] cli.py — corp project list/show/open, corp vault validate, corp doctor
- [x] tests — 44 tests passing (vault_io + project_resolver)
- [x] Stub config/agents.yaml and config/workflows.yaml (for Step 2)
- [ ] Commit, merge to main, tag v0.1.0

---

## What NOT To Build Yet

Per Council decision:
- ❌ Workflow engine (Step 2)
- ❌ Chat router (Step 3)
- ❌ Template registry (Step 4)
- ❌ Cross-project index (Step 2.5 — moved up from Step 5, but still after Step 2)
- ❌ Obsidian plugin
- ❌ Microsoft Graph API
- ❌ Vector embeddings
- ❌ Dynamic LLM workflow generation
