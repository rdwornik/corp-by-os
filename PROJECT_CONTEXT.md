# Corporate OS - Project Context

> This file is the source of truth. Read at the start of every session (UI or Claude Code).
> Last updated: 2025-01-15

---

## What is this project

Automation system for Technical Pre-Sales at Blue Yonder. File organization, recording processing, knowledge management, meeting briefs.

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
    │   ├── 10_Projects/           ← Active opportunities
    │   │   ├── _template/
    │   │   └── Company_Solution/
    │   ├── 20_Knowledge/
    │   ├── 30_Templates/
    │   ├── 80_Archive/            ← Archive INSIDE role
    │   │   ├── 2023/
    │   │   ├── 2024/
    │   │   └── 2025/
    │   └── 90_System/
    │
    ├── Archive_BDR/               ← Past role
    └── Archive_TechnicalConsultant/
```

**Why:**
- Archive inside role = full context when role changes
- Projects = `Company_Solution` (flat list, not nested)
- 30-40 opportunities/year - must be scalable

### 2. File Naming Convention

```
[TYPE]_[Description]_[YYYY-MM-DD]_[vNN].ext
```

| Element | Format | Example |
|---------|--------|---------|
| TYPE | PascalCase | `MeetingNotes`, `Presentation`, `Recording` |
| Description | PascalCase with `_` | `Discovery_Workshop`, `Technical_Review` |
| Date | ISO with dash | `2025-01-15` |
| Version | `_vNN` | `_v01`, `_v02` (optional) |
| Separator | `_` underscore | Always |

**File types:**
- `MeetingNotes` - meeting notes
- `Recording` - audio/video recordings
- `Transcript` - transcriptions
- `Presentation` - PPT files
- `Document` - Word docs, reports
- `RFP` - RFP documents
- `Email` - saved emails
- `Screenshot` - screenshots
- `Diagram` - architecture diagrams

**Examples:**
```
MeetingNotes_Discovery_Workshop_2025-01-15.md
Presentation_Architecture_Overview_2025-01-18_v02.pptx
Recording_Technical_Review_2025-01-20.mkv
RFP_Response_Final_2025-01-28_v03.docx
```

**Why type first:**
- Sort by type = all MeetingNotes together
- Within type = chronological (date at end)
- Easy filtering in file explorer

### 3. Project Naming

```
Company_Solution
```

**Examples:**
- `Honda_PALOMA`
- `PepsiCo_EMEA`
- `NEOM_WMS`
- `Corning_Planning`

**Why:**
- No dates in folder name (too many projects)
- Archive entire folder to `80_Archive/YYYY/`

### 4. Repository Location

```
C:\Dev\corporate-os\    ← OUTSIDE OneDrive (hidden from IT admin)
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

### LLM Routing

```
┌─────────────────┐
│  Sensitivity    │  ← ALWAYS local (Ollama)
│  Check          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ RESTRICTED/HIGH │──→ Ollama (local)
│ MEDIUM/LOW      │──→ Claude/Gemini (cloud)
└─────────────────┘
```

### Providers

- **Local:** Ollama (llama3.2, mistral, nomic-embed-text)
- **Cloud:** Claude (Sonnet), Gemini (Pro)
- **Embeddings:** Always local (nomic-embed-text)

---

## Current State

### Done
- [x] Folder architecture designed
- [x] Naming convention established
- [x] Repo initialized (C:\Dev\corporate-os)
- [x] Settings.py with correct paths
- [x] LLM Router with sensitivity check
- [x] Providers: Ollama, Claude, Gemini
- [x] OneDrive folders created (setup_mywork.ps1)

### In Progress
- [ ] Test Ollama connection
- [ ] pip install -e .

### To Do
- [ ] Copy PROJECT_CONTEXT.md to repo
- [ ] Vector Store (Chroma wrapper)
- [ ] FileOrganizer agent (dry-run reorganization)
- [ ] Migrate from Knowledge Hub to new structure
- [ ] Inbox Agent (process 00_Inbox)
- [ ] Search Agent
- [ ] Brief Agent

---

## Open Questions

1. **Ollama** - which models installed? Is it running?
2. **API keys** - ready? (Anthropic, Google, Graph)
3. **Existing recordings** - how many? Where? (mentioned 30+ .mkv)
4. **Knowledge Hub** - what to migrate first?

---

## How We Work

### Claude UI (this interface)
- Planning, architecture
- Discussion, feedback
- Document analysis
- Decisions requiring research

### Claude Code (CLI)
- Code implementation
- Debugging, testing
- Git operations
- File migration
- Multi-file work

### Principles
1. **Dry-run** always before changes
2. **Commit** regularly
3. **Challenge** - question decisions mutually
4. **Research** - key decisions get consulted (web search, multi-LLM)

---

## Context from Previous Sessions

### Transcripts
- V1-V3 architecture design
- V4 folder structure refinement
- Naming convention research and decision

### Rob's Existing Projects (to integrate)
- corporate-knowledge-extractor - recordings to reports pipeline
- sca-time-automation - time tracking from Outlook
- rfp-agent-cognitive-planning - RFP processing

---

## Next Session

**Priority:** FileOrganizer agent
1. Scan folder structure
2. Analyze content (local LLM)
3. Propose new names (dry-run)
4. After approval - move/rename

**Start:** Claude Code in C:\Dev\corporate-os
