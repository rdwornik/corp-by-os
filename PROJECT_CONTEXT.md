# Corporate OS - Project Context

> This file is the source of truth. Read at the start of every session (UI or Claude Code).
> Last updated: 2026-02-18

---

## What is this project

Automation system for Technical Pre-Sales at Blue Yonder. Goal: migrate entire OneDrive to clean MyWork structure, then build agents for file organization, recording processing, and knowledge management.

**Owner:** Rob (Technical Pre-Sales Engineer, Blue Yonder, Poland)

**Language policy:**
- Conversation: Polish or English (whatever is convenient)
- Code, comments, docs, markdown, commits: **Always English**

---

## Key Decisions

### 1. Folder Structure

```
OneDrive - Blue Yonder/
└── MyWork/
    ├── 00_Tech_PreSales/          ← Current role
    │   ├── 00_Inbox/
    │   │   ├── recordings/
    │   │   ├── documents/
    │   │   └── emails/
    │   ├── 10_Projects/           ← Active opportunities (Company_Solution per deal)
    │   ├── 20_Knowledge/
    │   ├── 30_Templates/
    │   │   ├── emails/
    │   │   ├── presentations/
    │   │   └── rfp/
    │   ├── 80_Archive/            ← Archive INSIDE role (YYYY/)
    │   └── 90_System/
    │
    ├── Archive_BDR/
    ├── Archive_TechnicalConsultant/
    ├── Archive_SalesAcademy/
    │   ├── Academy_2020_TC/
    │   ├── Academy_2021_Mentor/
    │   └── Academy_2022_Sales/
    ├── Archive_ExtraInitiatives/
    │   └── Marketing/
    ├── Archive_Admin/
    └── Camera_Roll/
```

### 2. File Naming Convention

```
TYPE_Description_YYYY-MM-DD[_vNN].ext
```

| Element | Format | Example |
|---------|--------|---------|
| TYPE | SHORT_CAPS | `PRES`, `DOC`, `RFP`, `NOTES`, `REC`, `DATA`, `EMAIL`, `IMG` |
| Description | PascalCase with `_` | `Discovery_Workshop`, `Technical_Review` |
| Date | ISO with dash | `2025-01-15` |
| Version | `_vNN` (optional) | `_v01`, `_v02` |
| Separator | `_` underscore | Always |

**File type codes:**
- `PRES` — presentations (.pptx)
- `DOC` — Word docs, reports (.docx, .pdf)
- `RFP` — RFP documents
- `NOTES` — meeting notes (.md, .docx)
- `REC` — audio/video recordings (.mkv, .m4a, .mp4)
- `DATA` — spreadsheets, data files (.xlsx, .csv)
- `EMAIL` — saved emails
- `IMG` — images, diagrams

**Examples:**
```
PRES_Honda_PALOMA_Discovery_2025-01-15.pptx
DOC_Architecture_Overview_2025-01-18_v02.docx
REC_Technical_Review_2025-01-20.mkv
RFP_Response_Final_2025-01-28_v03.docx
NOTES_Discovery_Workshop_2025-01-15.md
```

### 3. Project Naming

```
Company_Solution
```

**Examples:** `Honda_PALOMA`, `PepsiCo_EMEA`, `NEOM_WMS`, `Corning_Planning`

### 4. Repository Location

```
C:\os\corporate-os\    ← OUTSIDE OneDrive
```

### 5. Separators

| Context | Separator |
|---------|-----------|
| Business files | `_` underscore |
| Numbered folders | `_` underscore |
| Inside date | `-` dash (ISO) |
| Git branches | `-` dash |
| Python code | `_` underscore |

---

## Technical Architecture

### LLM

- **Primary:** Claude Sonnet 4 API (claude-sonnet-4-20250514)
- **Large files:** Gemini CLI (for reading big documents locally)
- **No Ollama** — dropped (too slow on CPU, poor quality)
- **No hybrid routing** — no sensitivity check, no multi-provider router

### Simple Client

```
src/core/llm/sonnet.py   ← single entry point
```

Methods: `complete(prompt, system)`, `complete_json(prompt, schema)`

### Code Standards

- Python, clean code, type hints
- `.env` for all secrets and paths (no hardcoded values)
- YAML for configs
- Pydantic for settings
- **Dry-run by default** on all destructive operations
- Git commits after every meaningful change

---

## Migration — Locked Decisions

| # | Decision |
|---|----------|
| 1 | ALL operations are COPY, not MOVE. Originals stay until migration confirmed. |
| 2 | Unclassifiable files → `00_Inbox/` |
| 3 | Old role archives → copy as-is, zero rename |
| 4 | Short file type codes: PRES, DOC, RFP, NOTES, REC, DATA, EMAIL, IMG |
| 5 | Naming: `TYPE_Description_YYYY-MM-DD[_vNN].ext` |
| 6 | Rename only local files. SharePoint copies stay as-is. |
| 7 | Knowledge Hub — deferred |
| 8 | Teams Chat Files (405) — triage with AI, extract valuable ones to MyWork |
| 9 | Pictures/Screenshots (1,389) — DELETE. Camera Roll (796) — KEEP. |
| 10 | Repo: clean structure, no Ollama, migration scripts in `scripts/` |

---

## Migration Phases

### Phase 0: Delete screenshots
- `Pictures/Screenshots/` → DELETE (1,389 files)

### Phase 1: Archives — COPY as-is (no rename)

| Source | Destination |
|--------|-------------|
| `Projects/_Academy 2020 TC/` | `MyWork/Archive_SalesAcademy/Academy_2020_TC/` |
| `Projects/_Academy 2021 Mentor/` | `MyWork/Archive_SalesAcademy/Academy_2021_Mentor/` |
| `Projects/_Academy 2022 Sales/` | `MyWork/Archive_SalesAcademy/Academy_2022_Sales/` |
| `Projects/_Inbound BDR/` | `MyWork/Archive_BDR/` |
| `Projects/_Technical Consultant/` | `MyWork/Archive_TechnicalConsultant/` |
| `Projects/_BY Extra Initiatives/` | `MyWork/Archive_ExtraInitiatives/` |
| `Projects/_BY Admin/` | `MyWork/Archive_Admin/` |
| `Projects/Marketing/` | `MyWork/Archive_ExtraInitiatives/Marketing/` |
| `Pictures/Camera Roll/` | `MyWork/Camera_Roll/` |
| `Recordings/` (old 2021-2022) | `MyWork/Archive_TechnicalConsultant/Recordings/` |

### Phase 2: Technical Presales — COPY + RENAME
- Source: `Projects/_Technical Presales/`
- Presentations (260 pptx): parse filename → extract client + date → `PRES_Description_YYYY-MM-DD.pptx`
- Other subfolders: Demo Scripts, RFPs, Knowledge → classify with Sonnet API

### Phase 3: Teams Chat Files — AI TRIAGE
- 405 files, mixed content
- AI classifies: keep (+ destination) or discard
- Output CSV for review before execution

### Phase 4: Remaining folders
- Attachments, Saved Emails, Meetings, Tmp, Zoom
- Small batches, semi-manual

### Phase 5: Knowledge Hub — LATER (deferred)

---

## OneDrive Base Path

```
C:\Users\1028120\OneDrive - Blue Yonder\
```

Set via env var `CORP_ONEDRIVE_PATH`.

---

## Current State

### Done
- [x] Folder architecture designed
- [x] Naming convention established
- [x] Repo initialized and pushed to GitHub (https://github.com/rdwornik/-corporate-os)
- [x] Settings.py with Pydantic
- [x] Branch v2-migration created
- [x] Removed: Ollama provider, sensitivity checker, hybrid LLM router
- [x] Created: Sonnet API client (src/core/llm/sonnet.py)

### In Progress
- [ ] Phase 1 archive copy script

### To Do
- [ ] Phase 0: Delete screenshots script
- [ ] Phase 2: Technical Presales rename script
- [ ] Phase 3: Teams Chat triage script
- [ ] FileOrganizer agent (v2, Sonnet-based)
- [ ] Inbox Agent
- [ ] Knowledge Hub (deferred)

---

## How We Work

### Claude UI
- Planning, architecture, discussion, decisions

### Claude Code (CLI)
- Code implementation, git, file migration, multi-file work

### Principles
1. **Dry-run** always before destructive changes
2. **Commit** regularly (after each meaningful change)
3. **Challenge** — question decisions mutually
4. **COPY not MOVE** — originals stay until confirmed
