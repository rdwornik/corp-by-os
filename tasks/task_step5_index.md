# Task: Step 5 — Cross-Project Index + Query

## Context

Corp-by-os v0.4.0 has: vault_io, project_resolver, 8 workflows, task manager, chat router, template registry. 221 tests. Lenzing pilot has 992 facts in facts.yaml, 123 notes in Obsidian.

**The problem:** All knowledge is per-project. No way to ask "which clients asked about SAP integration?" or "what's the most common product bundle?" without manually opening 30 folders.

**The solution (from AI Council):** SQLite index in `%LOCALAPPDATA%\corp-by-os\index.db` (NOT in OneDrive — avoids sync conflicts). Rebuilt on demand from all `project-info.yaml` + `facts.yaml` files.

---

## What to Build

### 1. Index Builder (`src/corp_by_os/index_builder.py`)

```python
"""Cross-project SQLite index.

Aggregates project-info.yaml + facts.yaml from all projects into a single
queryable database. Lives in %LOCALAPPDATA%\corp-by-os\index.db.

NOT in OneDrive — rebuilds are frequent, SQLite + OneDrive sync = corruption.
"""

def rebuild_index(config: AppConfig) -> IndexStats
    """Full rebuild: scan vault + OneDrive, aggregate into SQLite."""

def update_project(project_id: str, config: AppConfig) -> bool
    """Update single project in index (after extraction)."""

def get_index_path(config: AppConfig) -> Path
    """Returns %LOCALAPPDATA%/corp-by-os/index.db"""
```

### SQLite Schema

```sql
-- Project metadata (from project-info.yaml)
CREATE TABLE projects (
    project_id TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    status TEXT,              -- active | rfp | proposal | won | lost | archived
    products TEXT,            -- JSON array
    topics TEXT,              -- JSON array
    domains TEXT,             -- JSON array
    people TEXT,              -- JSON array
    region TEXT,
    industry TEXT,
    files_processed INTEGER,
    facts_count INTEGER,
    last_extracted TEXT,       -- ISO date
    onedrive_path TEXT,
    vault_path TEXT,
    updated_at TEXT            -- when this row was last updated
);

-- Individual facts (from facts.yaml)
CREATE TABLE facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    source TEXT,               -- source note ID
    source_title TEXT,
    topics TEXT,               -- JSON array
    domains TEXT,              -- JSON array
    products TEXT,             -- JSON array
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);

-- Full-text search on facts
CREATE VIRTUAL TABLE facts_fts USING fts5(
    fact, source_title, topics, project_id,
    content=facts, content_rowid=id
);

-- Index stats
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- Keys: last_rebuild, total_projects, total_facts, rebuild_duration_seconds
```

### 2. Query Engine (`src/corp_by_os/query_engine.py`)

```python
"""Query the cross-project index.

Three query modes:
1. Structured: filter by project, product, topic, domain
2. Full-text search: FTS5 on facts
3. Analytics: aggregations, patterns, rankings
"""

def search_facts(query: str, project_filter: str = None, limit: int = 20) -> list[FactResult]
    """Full-text search across all facts."""

def search_projects(products: list = None, topics: list = None, 
                    status: str = None, region: str = None) -> list[ProjectResult]
    """Structured project search."""

def get_analytics() -> AnalyticsReport
    """Cross-project patterns: top topics, product bundles, common integrations."""

def query_natural(question: str, config: AppConfig) -> QueryResult
    """Natural language query: keyword search → optional LLM summarization."""
```

### Models (add to models.py)

```python
@dataclass
class IndexStats:
    projects_indexed: int
    facts_indexed: int
    rebuild_duration: float
    index_path: str

@dataclass
class FactResult:
    project_id: str
    client: str
    fact: str
    source_title: str
    topics: list[str]
    relevance_score: float     # FTS5 rank

@dataclass
class ProjectResult:
    project_id: str
    client: str
    status: str
    products: list[str]
    topics: list[str]
    facts_count: int

@dataclass  
class AnalyticsReport:
    total_projects: int
    total_facts: int
    top_topics: list[tuple[str, int]]       # (topic, count) top 15
    top_products: list[tuple[str, int]]     # (product, count) top 10
    top_domains: list[tuple[str, int]]      # (domain, count) top 8
    product_bundles: list[tuple[str, int]]  # ("WMS + TMS", count)
    projects_by_status: dict[str, int]      # {active: 28, won: 5, ...}
    projects_by_region: dict[str, int]
    avg_facts_per_project: float
    stale_projects: list[str]               # no extraction > 30 days
```

### 3. CLI Commands

```
corp index rebuild                    Full rebuild from all projects
corp index stats                      Show index stats (projects, facts, age)
corp index rebuild --project Lenzing  Update single project

corp query "SAP integration"          Full-text search across facts
corp query --project Lenzing "demand" Search within one project
corp query --product WMS              All WMS projects
corp query --topic "Security"         All projects mentioning Security

corp analytics                        Show cross-project patterns
```

### 4. Chat Integration

New intents for chat router:

```yaml
# Add to workflows.yaml or handle directly in intent_router

query_knowledge:
  trigger_phrases: ["kto pytal o", "which clients", "znajdz", "search", "query", "szukaj"]
  
show_analytics:
  trigger_phrases: ["analytics", "analityka", "patterns", "wzorce", "statystyki", "stats"]
```

Chat examples:
```
You: Kto pytał o SAP integration?
Agent: → search_facts("SAP integration") → shows results grouped by project

You: Które projekty mają WMS?
Agent: → search_projects(products=["WMS"]) → list of WMS projects

You: Pokaż mi statystyki
Agent: → get_analytics() → top topics, products, patterns
```

### 5. Analytics Dashboard (Obsidian)

`corp analytics` also generates `00_dashboards/analytics.md`:

```markdown
---
title: Cross-Project Analytics
source_tool: corp-by-os
date: 2026-03-09
tags: [dashboard, auto-generated, analytics]
---

# Cross-Project Analytics

*Generated from {N} projects, {M} facts on {date}.*

## Top Topics
| Topic | Projects | Facts |
|---|---|---|
| Demand Planning | 18 | 342 |
| SAP Integration | 14 | 198 |
| Security & Compliance | 12 | 156 |
| ...

## Product Distribution
| Product | Projects |
|---|---|
| Planning (DSP/IBP) | 15 |
| WMS | 8 |
| TMS | 5 |
| ...

## Common Product Bundles
| Bundle | Count |
|---|---|
| WMS + TMS | 4 |
| Planning + Network | 3 |
| ...

## Projects by Status
| Status | Count |
|---|---|
| active | 28 |
| archived | 5 |
| ...
```

---

## Data Sources

### project-info.yaml (from cpe render, in vault 01_projects/)
Already have Lenzing. After batch extraction of 30 projects, all will have this.

### facts.yaml (from cpe render, in vault 01_projects/ or OneDrive _knowledge/)
Lenzing has 992 facts. Other projects: 0 until extracted.

### Fallback for projects without extraction
If no facts.yaml exists, still index project-info.yaml metadata (from COM or folder name parsing). Show `facts_count: 0` in results.

---

## Index Rebuild Logic

```python
def rebuild_index(config):
    db = sqlite3.connect(index_path)
    db.execute("PRAGMA journal_mode=WAL")  # concurrent reads during rebuild
    
    # Clear and rebuild
    db.execute("DELETE FROM facts")
    db.execute("DELETE FROM projects")
    
    # Scan both OneDrive projects and vault projects
    for project_id in all_project_ids:
        # Try vault first (has richer data)
        project_info = read_from_vault(project_id) or read_from_onedrive(project_id)
        if project_info:
            insert_project(db, project_info)
        
        # Load facts if available
        facts = load_facts_yaml(project_id)
        if facts:
            insert_facts(db, project_id, facts)
    
    # Rebuild FTS index
    db.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
    
    # Update meta
    db.execute("INSERT OR REPLACE INTO meta VALUES ('last_rebuild', ?)", [now()])
    db.commit()
```

---

## Implementation Checklist

- [ ] Branch: feature/cross-project-index
- [ ] models.py — add IndexStats, FactResult, ProjectResult, AnalyticsReport
- [ ] index_builder.py — SQLite schema creation, rebuild, update_project
- [ ] query_engine.py — search_facts (FTS5), search_projects (structured), get_analytics
- [ ] built_in_actions.py — add rebuild_index, query_knowledge, show_analytics actions
- [ ] Update cli.py — corp index rebuild/stats, corp query, corp analytics
- [ ] Update intent_router.py — add query_knowledge and show_analytics intents
- [ ] Update chat.py — handle query results display
- [ ] Generate 00_dashboards/analytics.md on analytics run
- [ ] test_index_builder.py — rebuild, update, schema validation
- [ ] test_query_engine.py — FTS search, structured filters, analytics
- [ ] Real test: rebuild index with Lenzing data (992 facts)
- [ ] Real test: `corp query "SAP"`, `corp query --product WMS`, `corp analytics`
- [ ] Real test: `corp chat` → "kto pytał o SAP?"
- [ ] Commit, merge to main, tag v0.5.0

---

## Important Notes

- **SQLite in %LOCALAPPDATA%, NOT OneDrive.** This is the single most important design decision from Council. OneDrive + frequently rewritten SQLite = corruption.
- **WAL mode** for concurrent reads during rebuild.
- **FTS5** for fast full-text search on facts. No vector embeddings needed yet.
- **Rebuild is fast** — 30 projects × ~1000 facts each = ~30K rows. SQLite handles this in seconds.
- **Only Lenzing has facts now.** Other projects will show project metadata only until batch extraction runs. That's fine — index grows as extraction coverage grows.
- **Analytics is the killer feature.** "80% of manufacturing RFPs ask about SAP" — this is what makes the system invaluable.
