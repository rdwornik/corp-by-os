# Task: Step 3 — Chat Router

## Context

Corp-by-os v0.2.0 has: vault_io, project_resolver, workflow engine (8 workflows), task manager. 117 unit tests, 12/12 integration tests. Now we add `corp chat` — natural language interface that routes to workflows.

**Already working:**
```powershell
corp run new_opportunity --client Siemens --product WMS --contact "Hans Mueller"
corp run attention_scan
corp run prep_deck --project Lenzing --topic "Demo" --date 2026-03-15
corp task add "Brief for Lenzing" --deadline 2026-03-14 --priority high
corp tasks
```

**What we're adding:**
```
corp chat

You: Mam nowe opportunity, Siemens, WMS, kontakt Hans Mueller
Agent: → routes to new_opportunity workflow → executes

You: Co wymaga mojej uwagi?
Agent: → routes to attention_scan → shows dashboard

You: Muszę przygotować prezentację demo dla Lenzing na piątek
Agent: → routes to prep_deck → creates deck

You: Co mam do zrobienia?
Agent: → routes to my_tasks → shows task list
```

---

## Architecture (from AI Council decision)

**Two-stage routing:**
1. **Keyword/rule matcher** — covers ~70% of inputs. Fast, free, deterministic.
2. **LLM fallback** (Gemini Flash) — for ambiguous inputs. Structured JSON output.

**LLM is NEVER an executor.** It outputs `{workflow_id, parameters}` only. Workflow engine executes.

---

## New Modules

### `src/corp_by_os/intent_router.py`

```python
"""Two-stage intent routing: keywords first, LLM fallback.

Stage 1: Match user input against trigger_phrases from workflows.yaml
         + task-specific patterns. Fast, free, deterministic.

Stage 2: If no match, call Gemini Flash with available workflows
         as context. Returns structured Intent.
"""

@dataclass
class Intent:
    workflow_id: str | None     # None = chitchat/unclear
    parameters: dict            # extracted entities
    confidence: float           # 0.0 - 1.0
    source: str                 # "keyword" | "llm" | "none"
    response_text: str | None   # LLM's suggested response for chitchat

def route(user_input: str, workflows: dict, context: dict | None = None) -> Intent
def _keyword_match(user_input: str, workflows: dict) -> Intent | None
def _llm_match(user_input: str, workflows: dict, context: dict | None = None) -> Intent
```

#### Keyword Matching Logic

Each workflow in workflows.yaml has `trigger_phrases`. Match logic:

1. Normalize input: lowercase, strip diacritics (ą→a, ś→s, etc.), strip punctuation
2. For each workflow, check if ANY trigger phrase is a substring of normalized input
3. If match found, extract parameters using simple patterns:
   - Client name: check against known project list (from project_resolver)
   - Product: match against known product list (Planning, WMS, TMS, CatMan, etc.)
   - Date: regex for YYYY-MM-DD, "jutro", "piątek", "za tydzień", "tomorrow", "next friday"
   - Topic: remaining meaningful words after removing trigger phrase and known entities
   - Priority: "pilne"/"urgent" → high, "ważne" → medium
   - Reason: after "bo"/"because" or for archive: won/lost/cancelled
4. If multiple workflows match, prefer longest trigger phrase match
5. Task shortcuts: "muszę"/"need to"/"zaplanuj" → add_task even without explicit trigger

#### Parameter Extraction Patterns

```python
PRODUCT_ALIASES = {
    "planning": "Planning", "wms": "WMS", "tms": "TMS",
    "catman": "CatMan", "network": "Network", "e2e": "E2E",
    "ibp": "Planning", "siop": "Planning", "flexis": "Flexis",
}

DATE_PATTERNS = {
    "dzisiaj": today, "today": today,
    "jutro": today+1, "tomorrow": today+1,
    "pojutrze": today+2,
    "w piatek": next_friday, "piątek": next_friday,
    "w poniedziałek": next_monday,
    r"\d{4}-\d{2}-\d{2}": parsed_date,
    r"\d{1,2}\s+(marca|kwietnia|maja)": parsed_polish_date,
}
```

### `src/corp_by_os/llm_router.py`

```python
"""Gemini Flash LLM for intent classification.

Called ONLY when keyword matching fails.
Returns structured JSON parsed into Intent.
"""

def classify_intent(user_input: str, workflows: dict, context: dict | None = None) -> Intent
```

#### Gemini System Prompt

```
You are a workflow router for a pre-sales engineer's productivity system.
Given the user's message, determine which workflow to execute and extract parameters.

Available workflows:
{workflows_summary}

Known projects: {project_list}
Known products: Planning, WMS, TMS, CatMan, Network, E2E, Flexis, Migration, Retail

The user speaks Polish or English. Respond in the same language.

Return ONLY valid JSON:
{
  "workflow_id": "workflow_id_or_null",
  "parameters": {
    "client": "string or null",
    "product": "string or null",
    "project": "string or null",
    "topic": "string or null",
    "date": "YYYY-MM-DD or null",
    "priority": "high|medium|low or null",
    "reason": "string or null",
    "title": "string or null",
    "notes": "string or null"
  },
  "confidence": 0.0-1.0,
  "response_text": "Human-friendly response if chitchat or clarification needed"
}

If the message is chitchat or unclear, set workflow_id to null and provide response_text.
```

#### Cost Controls
- Gemini 2.5 Flash (cheapest model)
- Cache: if same normalized input seen before, return cached result
- Daily cap: max 30 LLM calls/day (configurable in .env)
- Track total spend in `%LOCALAPPDATA%\corp-by-os\usage.json`
- If cap reached: "Nie mogę przetworzyć — użyj `corp run <workflow>` bezpośrednio"

### `src/corp_by_os/chat.py`

```python
"""Interactive terminal chat loop.

Rich-based input/output. Maintains conversation context.
Routes via intent_router → workflow_engine.
"""

def chat_loop(config: AppConfig):
    """Main chat loop."""
    # 1. Show welcome banner
    # 2. Loop: input → route → preview → confirm if needed → execute → show result
    # 3. Handle: quit/exit/Ctrl+C
    # 4. Maintain last 5 turns for context (passed to LLM if needed)
```

#### Chat Flow
```
1. User types message
2. intent_router.route(message, workflows, context)
3. If workflow_id is None:
   - Show response_text (chitchat/clarification)
   - Continue loop
4. If workflow_id found:
   - Show preview: "Zamierzam wykonać: {workflow.description} z parametrami: {params}"
   - If workflow.confirmation: ask Y/n
   - Execute via workflow_engine
   - Show results (Rich formatted)
5. Store turn in history
```

#### Special Commands (bypass routing)
- `quit` / `exit` / `q` — exit chat
- `help` — show available workflows
- `status` — show project counts, pending tasks
- `!<command>` — pass directly to corp CLI (e.g., `!project list`)

---

## CLI Update

### New command: `corp chat`

```python
@cli.command()
@click.option("--no-llm", is_flag=True, help="Keyword matching only, no Gemini calls")
def chat(no_llm):
    """Interactive chat — natural language workflow routing."""
```

### Welcome Banner
```
╭─── Corp-by-os ────────────────────────────────────╮
│ Mów naturalnie po polsku lub angielsku.            │
│ Zarządzam projektami, prezentacjami, zadaniami.   │
│                                                    │
│ Przykłady:                                         │
│  • "Nowe opportunity, Siemens, WMS"               │
│  • "Przygotuj prezentację demo dla Lenzing"       │
│  • "Co wymaga mojej uwagi?"                       │
│  • "Co mam do zrobienia?"                         │
│                                                    │
│ quit = wyjście · help = pomoc · !cmd = bezpośredni │
╰────────────────────────────────────────────────────╯
```

---

## .env Additions

```bash
# Existing
VAULT_PATH=C:\Users\1028120\Documents\ObsidianVault
PROJECTS_ROOT=C:\Users\1028120\OneDrive - Blue Yonder\MyWork\10_Projects
ARCHIVE_ROOT=C:\Users\1028120\OneDrive - Blue Yonder\MyWork\80_Archive
APP_DATA_PATH=%LOCALAPPDATA%\corp-by-os

# NEW for Step 3
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-2.5-flash
LLM_DAILY_CAP=30
```

---

## Dependencies

Add to pyproject.toml:
```toml
[project.optional-dependencies]
llm = ["google-genai>=1.0"]
```

Install: `pip install -e ".[dev,llm]"`

Note: use `google.genai` (new SDK), NOT deprecated `google.generativeai`.

---

## Test Strategy

### test_intent_router.py (~20 tests)

**Keyword matching:**
- Polish: "nowe opportunity Siemens WMS" → new_opportunity {client: Siemens, product: WMS}
- English: "what needs attention" → attention_scan {}
- Polish: "przygotuj prezentację demo Lenzing piątek" → prep_deck {project: Lenzing, topic: demo, date: next_friday}
- Polish: "muszę zrobić brief na Lenzing do środy" → add_task {title: "brief na Lenzing", project: lenzing_planning, deadline: next_wednesday}
- Polish: "co mam do zrobienia" → my_tasks
- Polish: "archiwizuj Honda, przegrany" → archive_project {project: honda_wms, reason: lost}
- Polish: "brief na Lenzing" → project_brief {project: lenzing_planning}
- Ambiguous: "Lenzing" → clarify (multiple workflows could match)
- No match: "jaka jest pogoda" → None (chitchat)

**Parameter extraction:**
- Date: "15 marca" → 2026-03-15
- Date: "w piątek" → next Friday
- Date: "jutro" → tomorrow
- Product: "ibp" → Planning
- Priority: "pilne" → high
- Client fuzzy: "lenzing" → Lenzing_Planning project

**LLM fallback (mocked):**
- Mock Gemini response → verify Intent parsed correctly
- Mock Gemini failure → verify graceful degradation
- Mock daily cap reached → verify fallback message

### test_chat.py (~10 tests)
- Special commands: quit, help, status, !project list
- Conversation history maintained
- Confirmation flow for destructive workflows

### test_llm_router.py (~5 tests)
- Structured JSON parsing
- Invalid JSON handling
- Cost tracking

---

## Implementation Checklist

- [ ] Branch: feature/chat-router
- [ ] intent_router.py — keyword matching with trigger phrases + parameter extraction
- [ ] llm_router.py — Gemini Flash structured classification
- [ ] chat.py — Rich terminal loop with preview + confirm + execute
- [ ] Update cli.py — add `corp chat` command
- [ ] Update .env.example with GEMINI_API_KEY, LLM_DAILY_CAP
- [ ] Update pyproject.toml with google-genai optional dep
- [ ] test_intent_router.py — keyword + parameter extraction + fallback
- [ ] test_llm_router.py — mocked Gemini + error handling
- [ ] test_chat.py — special commands + flow
- [ ] Real test: `corp chat` with Polish input
- [ ] Real test: keyword-only mode `corp chat --no-llm`
- [ ] Commit, merge to main, tag v0.3.0

---

## Example Conversations (test scenarios)

### Polish — full flow
```
You: Mam nowe opportunity, firma Bosch, potrzebują WMS i TMS, kontakt Peter Schmidt
Agent: Zamierzam utworzyć nowe opportunity:
  Client: Bosch
  Product: WMS
  Contact: Peter Schmidt
  Kontynuować? [Y/n]
→ Y
Agent: ✅ Stworzone: Bosch_WMS/ + deck + metadata + vault skeleton

You: Muszę przygotować prezentację na discovery call z Bosch, w poniedziałek
Agent: ✅ Bosch_2026-03-10_Discovery.pptx stworzony

You: Co mam do zrobienia?
Agent: 🔴 HIGH
  □ Prepare brief for Lenzing (Mar 10)
  🟡 MEDIUM
  □ Discovery call Bosch deck (Mar 10)
```

### English — attention scan
```
You: What needs my attention?
Agent: ⚠️ Attention Dashboard:
  🔴 HIGH: 5 projects with no extraction
  🟡 MEDIUM: 3 projects stale (>30 days)
  Generated: 00_dashboards/attention.md
```

### Chitchat — graceful handling
```
You: Cześć, jak się masz?
Agent: Hej! Jestem gotowy do pracy. Czym mogę pomóc?
  Przykłady: "nowe opportunity", "co wymaga uwagi", "moje taski"
```
