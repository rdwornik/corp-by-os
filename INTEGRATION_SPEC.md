# Integration Specification — Corp-by-os Agent Ecosystem

> Version: 1.0 | Last updated: 2026-03-08
>
> This document is THE contract all agents in the ecosystem follow.

---

## 1. Obsidian Vault Structure

**Path:** `C:\Users\1028120\Documents\ObsidianVault`
**Sync:** Obsidian Sync Standard ($4/mo, vault: corp-brain)
**Git backup:** corporate GitHub (TBD)

### Folder Mutability Rules

| Folder | Purpose | Mutability | Writer |
|---|---|---|---|
| `00_dashboards/` | Dataview queries, status overviews, generated reports | REGENERABLE | corp-by-os (attention_scan workflow) |
| `01_projects/` | Per-client: project-info.yaml, index.md, facts.yaml | REGENERABLE | cpe render, corp-by-os vault_io |
| `02_sources/` | Immutable extracted knowledge notes | IMMUTABLE (append-only) | cke process, cpe extract-cke |
| `03_playbooks/` | Curated RFP answers, demo scripts | PROTECTED (human-curated) | corp-rfp-agent, human |
| `04_evergreen/` | Synthesized "current truth" per topic | REGENERABLE | future synthesis pipeline |
| `05_templates/` | Note templates, presentation templates index | PROTECTED | human, com prep-deck |
| `_assets/` | Images, attachments | APPEND-ONLY | cke (slide frames) |

### Rules

- **IMMUTABLE** folders: once written, never overwritten. New versions get new filenames.
- **REGENERABLE** folders: can be fully rebuilt from source data. Safe to delete and re-run.
- **PROTECTED** folders: human content. Agents may ADD but never DELETE or OVERWRITE existing files.

---

## 2. Project Identity Standard

Every project MUST have a stable `project_id` in its `project-info.yaml`.

**Format:** `{client_slug}_{product_slug}`
**Examples:** `lenzing_planning`, `honda_wms`, `stellantis_e2e`

### Rules

- Lowercase, underscores, no spaces
- Matches folder name in `10_Projects/` (OneDrive) and `01_projects/` (Obsidian)
- Once assigned, NEVER changes (even if client renames opportunity)
- `project-info.yaml` is the canonical source of project metadata

### OneDrive Folder Structure (MyWork)

| Folder | Purpose |
|---|---|
| `10_Projects/` | Active project folders (one per opportunity) |
| `40_Assets_Recordings/` | Shared assets, meeting recordings |
| `60_Source_Library/` | Reference materials, templates, standards |
| `80_Archive/_Legacy_Roles/` | Archived projects from previous roles |
| `80_Archive/{year}/` | Year-based archive of completed projects |

### project-info.yaml Required Fields

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

---

## 3. Agent Registry

Each agent is invoked via CLI subprocess. **NEVER import agent code directly.**

| Agent | CLI | Install | Key Commands |
|---|---|---|---|
| corp-os-meta | `corp-meta` | `pip install -e` | validate, normalize, report |
| corp-knowledge-extractor | `cke` | `pip install -e` | process, process-manifest |
| corp-project-extractor | `cpe` | `pip install -e` | scan, extract-cke, render |
| corp-opportunity-manager | `com` | `pip install -e` | new, list, show, prep-deck, chat |
| corp-rfp-agent | (TBD) | — | (legacy, needs rewire) |
| ai-council | `council` | `python -m src.cli` | (question as arg or file) |
| corp-pdf-toolkit | `pdf2md` | `python pdf2md.py` | (file as arg) |

### Invocation Rules

- Always `subprocess.run(shell=False)` with explicit executable
- Capture stdout + stderr, log both
- For payloads >4KB: write JSON to `%LOCALAPPDATA%\corp-by-os\jobs\job_<uuid>.json`, pass path
- Validate all arguments before calling subprocess
- Never pass raw LLM output to subprocess

---

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

---

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
