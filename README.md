# corp-by-os

Root orchestrator for the Corporate OS agent ecosystem. A CLI tool (`corp`) that automates knowledge extraction, project tracking, vault management, template selection, and retrieval workflows for pre-sales engineering at Blue Yonder.

## Features

- **Knowledge index** -- SQLite FTS5 search across all extracted facts, projects, products, and topics
- **Project management** -- Fuzzy project resolution, metadata tracking, vault-backed task management
- **Extraction pipeline** -- Automated extraction from OneDrive project folders into Obsidian vault notes
- **Intelligent ingest** -- Content-registry-based file routing with LLM fallback classification
- **Unified retrieval** -- Client prep decks, RFP answer drafting, and discovery support with confidence-aware ranking
- **Template registry** -- Smart template scanning, registration, and goal-based selection
- **Cleanup and audit** -- OneDrive deduplication, file classification, disk space recovery, and LLM-powered project audits
- **Freshness tracking** -- Source-tracking scanner for vault note staleness
- **System doctor** -- Integrity checks for vault, index, ops.db, content_registry.yaml, and routing_map.yaml
- **Intent routing** -- Natural language to workflow mapping (keyword match + Gemini Flash fallback)
- **Interactive chat** -- Conversational interface with vault context

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

### Project operations

```bash
corp project list --status active
corp project show "Lenzing"
corp project open "Lenzing"          # Open project folder in Explorer
```

### Search and retrieval

```bash
corp query "WMS implementation" --product "Luminate Planning"
corp retrieve "supply chain visibility" --client "Nestle" --format json
corp retrieve "demand planning" --top 5
corp analytics                       # Cross-project patterns
```

### Client intelligence

```bash
corp prep "Saint-Gobain"             # Generate client briefing
corp prep "Alfa Laval" --model gemini-2.0-flash
corp rfp answer "How does BY handle demand sensing?" --product "Luminate Planning"
corp rfp answer "Describe your SaaS deployment model" --client Lenzing
```

### Ingest pipeline

Files flow through: `ingest` (route) -> `classify` (LLM fallback) -> `finalize` (approve staged).

```bash
corp ingest                          # Scan 00_Inbox, route all files
corp ingest /path/to/folder --dry-run
corp classify --dry-run              # Preview LLM classifications for quarantined files
corp classify                        # Classify and stage
corp finalize --approve-all          # Move staged files to final destinations
```

### Extraction and overnight

```bash
corp extract /path/to/folder         # Extract knowledge via CKE
corp extract /path/to/folder --batch --dry-run
corp overnight --dry-run             # Batch extraction across non-project folders
corp overnight --scope source-library --budget 2.0
```

### Maintenance

```bash
corp doctor                          # Agent health + system integrity checks
corp vault validate
corp index rebuild
corp index stats
corp freshness --verbose
corp cleanup                         # Disk space analysis (plan only)
corp cleanup --scope duplicates --execute
corp cleanup-scan --output moves.yaml  # Scan for misplaced files
corp apply-moves moves.yaml --dry-run  # Preview proposed file moves
corp apply-moves moves.yaml            # Execute approved moves
corp audit --budget 0.30             # Full MyWork scan + Gemini analysis
corp audit --skip-gemini             # Scan and coverage only
```

### Templates and tasks

```bash
corp template list
corp template scan                   # Scan 30_Templates/, update registry
corp template select "customer presentation"

corp task add "Follow up on pricing" --project lenzing --priority high
corp task list --status todo --project lenzing
corp task done "Follow up on pricing"
corp tasks                           # Shorthand for task list
```

### Workflows and chat

```bash
corp run --list                      # List available workflows
corp run prep_deck                   # Execute a workflow
corp chat                            # Interactive NL interface
corp chat --no-llm                   # Keyword matching only
```

## Architecture

```
src/corp_by_os/
  cli.py              # Click CLI -- all commands
  config.py           # AppConfig (.env + config/agents.yaml)
  models.py           # Core dataclasses (ProjectInfo, Task, Workflow, VaultZone, etc.)
  vault_io.py         # Sole writer to Obsidian vault (zone-aware, idempotent)
  query_engine.py     # SQLite FTS5 search
  index_builder.py    # Knowledge index builder (BM25 ranking, trust_level)
  workflow_engine.py  # Multi-step workflow executor
  intent_router.py    # Two-stage routing: keyword match -> LLM fallback
  llm_router.py       # Gemini Flash integration (google-genai SDK)
  project_resolver.py # Fuzzy project name resolution
  template_manager.py # Template scanning, registration, selection
  task_manager.py     # Task CRUD via vault notes
  audit.py            # Read-only MyWork audit with LLM analysis
  built_in_actions.py # Python-callable workflow actions
  chat.py             # Interactive chat loop

  cleanup/            # OneDrive cleanup: scan -> classify -> propose -> execute
  doctor/             # System integrity checks (vault, index, ops.db, YAML configs)
  extraction/         # Non-project file extraction (folder policies, routing, manifests)
  freshness/          # Source-tracking vault note staleness scanner
  ingest/             # Intelligent file routing (content registry + LLM classifier)
  overnight/          # Batch extraction orchestrator (preflight, dedup, safety, state)
  ops/                # Operational state: asset tracking, ingest events, registry suggestions
  retrieve/           # Unified retrieval: prep decks, RFP answers, confidence ranking
```

### Data stores

- **OneDrive** (`MyWork/`) -- source files, templates, project folders
- **Obsidian vault** -- extracted notes with zone-based mutability (`02_sources` immutable, `00_dashboards` regenerable)
- **index.db** (`%LOCALAPPDATA%/corp-by-os/`) -- SQLite FTS5 search over facts, with BM25 ranking and `trust_level` metadata
- **ops.db** (`%LOCALAPPDATA%/corp-by-os/`) -- 4 tables: `assets`, `packages`, `ingest_events`, `registry_suggestions`. Every state change logged for undo support
- **content_registry.yaml** (`config/`) -- known content series with naming patterns, destinations, and default metadata. Drives the ingest router for file placement in MyWork
- **routing_map.yaml** (`MyWork/90_System/`) -- maps MyWork folders to vault zones. Drives extraction for note placement in the vault

### Key design decisions

- **corp-by-os is the sole vault writer** -- other tools (CKE, project-extractor) are invoked via subprocess but never write directly to the vault
- **Two routing layers** -- `content_registry.yaml` routes files to MyWork destinations; `routing_map.yaml` routes extracted notes to vault zones
- **FTS5 first, embeddings deferred** -- full-text search with BM25 ranking handles current scale; vector search is a future option
- **Confidence-aware ranking** -- retrieval results are reranked by `trust_level`: verified (best) > extracted > generated > draft (most penalized)
- **Undo is first-class** -- every ingest action is logged in `ops.db` with `reversible=1` by default
- **Zero manual triage** -- the ingest pipeline (`ingest` -> `classify` -> `finalize`) aims to route all incoming files automatically, with human review only for low-confidence classifications
- **Index and ops.db live in `%LOCALAPPDATA%`** -- never in OneDrive (sync corruption risk)

## Testing

```bash
py -m pytest           # 680 tests, ~19s
py -m pytest -x -q     # stop on first failure
py -m ruff check src/  # lint (clean)
```

Tests use `tmp_path` fixtures and monkeypatching -- no real filesystem or API calls.

## Ecosystem

| Repo | Purpose | Tests |
|---|---|---|
| **corp-by-os** | Root orchestrator, CLI, vault writer | 680 |
| **corp-os-meta** | Shared metadata schemas and taxonomy | 87 |
| **corp-knowledge-extractor** | Tiered Gemini extraction pipeline | 421 |
| **corp-rfp-agent** | AI-powered RFP response automation | 151 |
| **corp-project-extractor** | Project folder scanning and structuring | 45 |
| **corp-opportunity-manager** | Opportunity lifecycle management | 61 |
| **ai-council** | Multi-model AI debate framework | 73 |
| **corp-pdf-toolkit** | PDF text extraction and anonymization | -- |
| **corp-sca-time-automation** | SCA time entry automation | 2 |
| **corp-ops** | Operational tooling | -- |

## Dependencies

| Package | Purpose |
|---|---|
| `click` | CLI framework |
| `rich` | Terminal UI (tables, panels, progress) |
| `pyyaml` | YAML config/data parsing |
| `python-dotenv` | `.env` file loading |
| `corp-os-meta` | Shared metadata schemas (local package) |
| `google-genai` | Gemini Flash LLM (optional, `pip install -e ".[llm]"`) |

## License

Internal use only -- Blue Yonder Pre-Sales Engineering
