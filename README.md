# corp-by-os

Root orchestrator for the Corporate OS agent ecosystem. A CLI tool (`corp`) that automates knowledge extraction, project tracking, vault management, template selection, and retrieval workflows for pre-sales engineering at Blue Yonder.

## Features

- **Knowledge Index** — SQLite FTS5 search across all extracted facts, projects, products, and topics
- **Project Management** — Fuzzy project resolution, metadata tracking, vault-backed task management
- **Extraction Pipeline** — Automated extraction from OneDrive project folders into Obsidian vault notes
- **Unified Retrieval** — Client prep decks, RFP answer drafting, and discovery support from the knowledge base
- **Template Registry** — Smart template scanning, registration, and goal-based selection
- **Cleanup & Audit** — OneDrive deduplication, file classification, and LLM-powered project audits
- **Freshness Tracking** — Source-tracking scanner for vault note staleness
- **System Doctor** — Integrity checks for vault, index, and operational state
- **Intent Routing** — Natural language to workflow mapping (keyword match + Gemini Flash fallback)
- **Interactive Chat** — Conversational interface with vault context

## Installation

```bash
# Clone (outside OneDrive to avoid sync issues)
git clone <repo> C:\Dev\corp-by-os
cd C:\Dev\corp-by-os

# Create venv and install
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Optional: LLM support (Gemini Flash)
pip install -e ".[llm]"

# Configure
cp .env.example .env
# Set: VAULT_PATH, PROJECTS_ROOT, TEMPLATES_ROOT, ARCHIVE_ROOT, APP_DATA_PATH
```

## Usage

```bash
# Project operations
corp project list --status active
corp project show "Lenzing"

# Search knowledge base
corp query "WMS implementation" --product "Luminate Planning"
corp retrieve "supply chain visibility" --client "Nestle" --format json

# Client intelligence
corp prep "Saint-Gobain" --model gemini
corp rfp answer "How does BY handle demand sensing?" --product "Luminate Planning"

# Extraction & processing
corp extract --project lenzing_planning
corp overnight --dry-run

# Maintenance
corp doctor
corp vault validate
corp index rebuild
corp freshness --verbose

# Interactive
corp chat
corp run prep_deck --client "PepsiCo"
```

## Architecture

```
src/corp_by_os/
  cli.py              # Click CLI — all commands
  config.py           # AppConfig (.env + YAML)
  models.py           # Core dataclasses
  vault_io.py         # Obsidian vault writer
  query_engine.py     # SQLite FTS5 search
  index_builder.py    # Knowledge index builder
  workflow_engine.py  # Multi-step workflow executor
  intent_router.py    # NL -> workflow routing
  cleanup/            # OneDrive cleanup pipeline
  doctor/             # System integrity checks
  extraction/         # File extraction pipeline
  freshness/          # Vault note staleness tracking
  ingest/             # Intelligent file routing
  overnight/          # Batch extraction orchestrator
  ops/                # Asset & state tracking
  retrieve/           # Prep decks, RFP, discovery
```

**Data stores:**
- **OneDrive** (`MyWork/`) — source files, templates, project folders
- **Obsidian vault** — extracted notes with zone-based mutability
- **SQLite index** (`%LOCALAPPDATA%/corp-by-os/index.db`) — FTS5 search
- **ops.db** — asset tracking and ingest event log

## Testing

```bash
py -m pytest           # 679 tests, ~19s
py -m pytest -x -q     # stop on first failure
py -m ruff check src/  # lint
```

## Related repos

- **corp-os-meta** — shared metadata schemas and types
- **corp-knowledge-extractor** — document extraction engine
- **corp-rfp-agent** — RFP response automation
- **ai-council** — multi-model debate framework

## License

Internal use only — Blue Yonder Pre-Sales Engineering
