# CLAUDE.md — corp-by-os

## What this repo does

Root orchestrator for the Corporate OS agent ecosystem. CLI tool (`corp`) that manages knowledge extraction, project tracking, vault I/O, template selection, cleanup, and retrieval across a pre-sales engineer's OneDrive + Obsidian vault workflow.

## Quick start

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Configure environment
cp .env.example .env   # set VAULT_PATH, PROJECTS_ROOT, etc.

# Run tests
py -m pytest

# Use CLI
corp project list
corp query "WMS implementation"
corp doctor
```

## Architecture

```
src/corp_by_os/
  cli.py              # Click CLI root — all commands
  config.py           # AppConfig from .env + config/agents.yaml
  models.py           # Dataclasses: ProjectInfo, Task, Workflow, VaultZone, etc.
  vault_io.py         # Single writer to Obsidian vault (zone-aware, idempotent)
  query_engine.py     # SQLite FTS5 search over facts/projects
  index_builder.py    # Builds index.db in %LOCALAPPDATA%/corp-by-os/
  workflow_engine.py  # Executes multi-step workflows from workflows.yaml
  intent_router.py    # Two-stage routing: keyword match → LLM fallback
  llm_router.py       # Gemini Flash integration (google-genai SDK)
  project_resolver.py # Fuzzy project name resolution
  template_manager.py # Template scanning, registration, selection
  task_manager.py     # Task CRUD via vault notes
  audit.py            # Read-only MyWork audit with LLM analysis
  built_in_actions.py # Python-callable workflow actions

  cleanup/            # OneDrive cleanup: scan → classify → propose → execute
  doctor/             # System integrity checks (vault, index, ops.db)
  extraction/         # Non-project file extraction pipeline
  freshness/          # Source-tracking vault note staleness scanner
  ingest/             # Intelligent file routing and extraction
  overnight/          # Fire-and-forget: extract + reshape + freshness
  ops/                # Operational state: asset tracking, ingest events
  retrieve/           # Unified retrieval: prep decks, RFP answers, discovery
```

### Data flow

- **OneDrive** (`MyWork/`) → source files, templates, projects
- **Obsidian vault** → extracted notes, dashboards, tasks (zone-based mutability)
- **SQLite index** (`%LOCALAPPDATA%/corp-by-os/index.db`) → FTS5 search over facts
- **ops.db** → asset tracking, ingest events, content routing state

### Key design decisions

- Index lives in `%LOCALAPPDATA%`, never OneDrive (sync corruption risk)
- Vault zones have mutability rules: `02_sources` is immutable, `00_dashboards` is regenerable
- Two-stage intent routing: keyword match first (fast, free), LLM fallback only when needed
- Dataclasses, not Pydantic (lightweight, frozen where appropriate)

## Dev standards

- Python 3.11+, Windows-first (`py -m`, `pathlib`)
- `pyproject.toml` as single source of truth for deps
- `ruff` lint + format, `pytest` for testing
- Feature branches, never commit directly to master
- Logging not print, dataclasses not dicts
- Config via `.env` + `config/agents.yaml` — never hardcode paths
- Click CLI, Rich terminal output
- Type hints everywhere, no bare `except:`

## Key commands

```bash
# Project management
corp project list [--status active]
corp project show <name>

# Knowledge retrieval
corp query "search terms" [--project X] [--product Y]
corp retrieve "query" [--client X] [--format json|table]
corp prep <client> [--model M]
corp rfp answer "question" [--client X]

# Vault & index
corp vault validate [--project X]
corp index rebuild [--project X]
corp index stats
corp analytics

# Extraction & processing
corp extract [--project X]
corp ingest [--project X]
corp overnight [--dry-run]
corp freshness [--verbose]

# Maintenance
corp doctor
corp cleanup [--scope all|duplicates] [--execute]
corp audit [--budget 0.30]

# Templates & tasks
corp template list | scan | select "goal"
corp task add "Title" [--project X] [--priority high]
corp tasks [--status todo]

# Interactive
corp chat [--no-llm]
corp run <workflow> [PARAMS]
```

## Test suite

```bash
py -m pytest           # run all tests
py -m pytest -x        # stop on first failure
py -m pytest -k "test_query"  # run specific tests
```

- **679 tests passing**, 1 skipped (as of 2026-03-15)
- Full coverage of: query engine, index builder, workflow engine, intent router, LLM router, vault I/O, template manager, project resolver, task manager, cleanup, doctor, freshness, ingest, overnight, retrieval, extraction
- Tests use `tmp_path` fixtures and monkeypatching — no real filesystem or API calls

## Dependencies

| Package | Purpose |
|---|---|
| `click` | CLI framework |
| `rich` | Terminal UI (tables, panels, progress) |
| `pyyaml` | YAML config/data parsing |
| `python-dotenv` | `.env` file loading |
| `corp-os-meta` | Shared metadata schemas (local package) |
| `google-genai` | Gemini Flash LLM (optional, `pip install -e ".[llm]"`) |

## API keys

Keys loaded globally from `Documents/.secrets/.env` via PowerShell profile.
Do NOT add API keys to local `.env`.
Check: `keys list` | Update: `keys set KEY value` | Reload: `keys reload`

This repo uses: `GEMINI_API_KEY` (audit, cleanup classifier, LLM router, overnight preflight/CKE client)

## Known issues

- **No test coverage** for: `config.py`, `cli.py`, `models.py`, `__main__.py`, `overnight/cke_client.py`, `extraction/vault_writer.py`
- Legacy code in `src/_cli_v1_old/`, `src/agents/`, `src/core/`, `src/services/` — not part of main `corp_by_os` package, candidates for removal
- No CI pipeline yet — tests run locally only

---

## Agent behavior rules

### Workflow
1. Plan mode for non-trivial tasks (3+ steps)
2. Use subagents for research and parallel analysis
3. After corrections: update `tasks/lessons.md`
4. Never mark done without proving it works
5. Feature branches, meaningful commits

### Core principles
- **Simplicity first** — minimal code impact
- **No laziness** — find root causes, no temp fixes
- **Minimal impact** — only touch what's necessary
- **Just fix it** — don't ask permission for obvious fixes

### Anti-patterns

| Don't | Do |
|---|---|
| Hardcoded paths | `.env` + YAML config |
| `print()` | `logging` module |
| Raw dicts | Dataclasses with type hints |
| Commit to master | Feature branch |
| Vague commits | "Fix rate limiter edge case in retry logic" |
| Over-engineer | Proportional effort to problem size |
