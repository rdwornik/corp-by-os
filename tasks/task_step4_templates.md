# Task: Step 4 — Template Registry + Smart Deck Preparation

## Context

Corp-by-os v0.3.0 has: vault_io, project_resolver, 8 workflows, task manager, chat router (keyword + Gemini fallback). 192 tests. `corp chat` works with Polish/English.

Currently `com prep-deck` copies ONE hardcoded template. We need:
1. A registry of all available templates
2. Gemini selects the right template based on context
3. `corp chat` understands "przygotuj prezentację o architekturze integracji"

**Templates location:** `C:\Users\1028120\OneDrive - Blue Yonder\MyWork\30_Templates\`

Current contents:
```
30_Templates/
├── Canonical/              ← master copies (just created, empty)
├── Working/                ← drafts
├── Deprecated/             ← old versions
├── Demo Scripts/           ← demo scripts subfolder
├── Prepare Presentation/   ← prep materials subfolder
├── Blue_Yonder_Corporate_Presentation_Deck.pptx (94MB — THE main deck)
└── Customer_Discovery_Questions.xlsx (25KB)
```

---

## What to Build

### 1. Template Registry (`90_System/template_registry.yaml`)

Scan `30_Templates/` and create a registry:

```yaml
# Template Registry — source of truth for all presentation templates
# Agents look up templates by ID, never by hardcoded path.
# Updated manually or via `corp template scan`.

templates:
  - id: corporate_deck
    name: "Blue Yonder Corporate Presentation Deck"
    file: "Blue_Yonder_Corporate_Presentation_Deck.pptx"
    path: "30_Templates/Blue_Yonder_Corporate_Presentation_Deck.pptx"
    size_mb: 94
    type: presentation
    use_cases:
      - discovery
      - corporate overview
      - executive briefing
      - first meeting
    domains: [Go-to-Market, Product]
    tags: [corporate, overview, discovery, executive]
    language: en

  - id: discovery_questions
    name: "Customer Discovery Questions"
    file: "Customer_Discovery_Questions.xlsx"
    path: "30_Templates/Customer_Discovery_Questions.xlsx"
    size_mb: 0.025
    type: questionnaire
    use_cases:
      - discovery call preparation
      - qualification
    domains: [Go-to-Market]
    tags: [discovery, questions, qualification]
    language: en

  # Future templates as they're added to 30_Templates/Canonical/
```

### 2. Template Scanner (`src/corp_by_os/template_manager.py`)

```python
"""Template registry management.

Scans 30_Templates/, maintains registry in 90_System/template_registry.yaml.
Selects best template for a given goal using keyword matching or LLM.
"""

def scan_templates(templates_root: Path) -> list[TemplateInfo]
    """Walk 30_Templates/, find all .pptx/.xlsx/.docx, build TemplateInfo list."""

def load_registry(registry_path: Path) -> list[TemplateInfo]
    """Load template_registry.yaml."""

def save_registry(registry_path: Path, templates: list[TemplateInfo])
    """Write template_registry.yaml."""

def select_template(goal: str, templates: list[TemplateInfo], use_llm: bool = False) -> TemplateInfo | None
    """Select best template for a goal.
    
    Stage 1: keyword match against use_cases + tags
    Stage 2: if no match and use_llm=True, ask Gemini Flash
    """

def copy_template(template: TemplateInfo, destination: Path, new_name: str) -> Path
    """Copy template to destination with new filename."""
```

### Models (add to models.py)

```python
@dataclass
class TemplateInfo:
    id: str
    name: str
    file: str
    path: str             # relative to MyWork root
    size_mb: float
    type: str             # presentation | questionnaire | document
    use_cases: list[str]
    domains: list[str]
    tags: list[str]
    language: str = "en"
```

### 3. Template Selection Logic

**Keyword matching (Stage 1):**
- User says "discovery" → match templates with "discovery" in use_cases or tags
- User says "demo" → match "demo" tag
- User says "RFP" → match "rfp" tag
- User says "architecture" or "technical" → match "technical", "architecture" tags
- User says "corporate overview" or "executive" → match "corporate", "executive" tags
- Multiple matches → prefer highest number of matching tags

**LLM selection (Stage 2, if keyword fails):**
- Send template registry summary + user's goal to Gemini Flash
- Gemini returns template_id
- Validate template_id exists in registry

**Fallback:** if no match and no LLM → use corporate_deck (the main 94MB deck)

### 4. Updated prep_deck Workflow

```yaml
id: prep_deck
description: "Prepare presentation — smart template selection"
trigger_phrases: ["przygotuj prezentacje", "prep deck", "prepare presentation", "nowa prezentacja"]
parameters:
  project: {type: string, required: true}
  topic: {type: string, required: true}
  date: {type: string, required: false, default: "today"}
  template_id: {type: string, required: false}  # override auto-selection
steps:
  - type: python
    description: "Select best template for topic"
    action: select_template_for_deck
  - type: python
    description: "Copy template to project folder with naming convention"
    action: copy_deck_to_project
```

The workflow now:
1. If `template_id` provided → use it directly
2. If not → `select_template(topic, registry)` picks the best match
3. Copies to `10_Projects/{project}/{Client}_{Date}_{Topic}.pptx`

### 5. New CLI Commands

```
corp template list                    Show all registered templates
corp template scan                    Scan 30_Templates/, update registry
corp template select "demo for WMS"   Show which template would be selected
```

### 6. Chat Integration

Already works via prep_deck workflow. Now with smart selection:

```
You: Przygotuj prezentację o architekturze platformy dla Lenzing
Agent: Wybieram szablon: Blue Yonder Corporate Presentation Deck (corporate_deck)
       → Lenzing_2026-03-09_Platform_Architecture.pptx
       Kontynuować? [Y/n]
```

---

## Implementation Checklist

- [ ] Branch: feature/template-registry
- [ ] models.py — add TemplateInfo dataclass
- [ ] template_manager.py — scan, load, save, select, copy
- [ ] built_in_actions.py — add select_template_for_deck, copy_deck_to_project actions
- [ ] Update workflows.yaml — enhanced prep_deck with template selection step
- [ ] Create 90_System/template_registry.yaml by scanning current 30_Templates/
- [ ] cli.py — add corp template list/scan/select commands
- [ ] test_template_manager.py — scan, keyword selection, fallback, copy
- [ ] Real test: `corp chat` → "przygotuj prezentację demo dla Lenzing"
- [ ] Real test: `corp template list` shows registry
- [ ] Commit, merge to main, tag v0.4.0

---

## Important Notes

- Template files are LARGE (94MB). Never read content — only copy.
- Registry lives in 90_System/ (not in vault, not in repo).
- `corp template scan` is manual — run when new templates added to 30_Templates/.
- Right now we only have 2 templates. Registry will grow as user adds more to Canonical/.
- Don't over-engineer selection for 2 templates. Keyword match + fallback to corporate_deck is sufficient. LLM selection becomes valuable at 10+ templates.
