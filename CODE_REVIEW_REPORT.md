# Code Review Report — corp-by-os

**Date:** 2026-03-15
**Branch:** `code-review-2026-03-15`
**Reviewer:** Claude Opus 4.6

---

## Summary

```
REPO:          corp-by-os (v0.3.0)
TESTS:         679 passed, 1 skipped, 0 failed
RUFF:          clean (0 errors)
COMMITS:       4
FILES CHANGED: 110
```

## Commits Made

| Commit | Description |
|---|---|
| `987f5a6` | style: ruff lint + format pass |
| `1d2b9d0` | docs: update CLAUDE.md to current state |
| `1a889a4` | docs: professional README with current architecture and usage |
| `902c7f8` | docs: document test coverage gaps in CLAUDE.md |

---

## Task 1: Repo Understanding

- **Purpose:** Root orchestrator for Corporate OS agent ecosystem — CLI tool (`corp`) for pre-sales knowledge management
- **Structure:** Main package `src/corp_by_os/` with 10 subpackages, legacy code in `src/agents/`, `src/core/`, `src/services/`
- **Tests:** 679 tests across 40+ test files, all passing
- **Entry point:** `corp` CLI via Click (`corp_by_os.cli:cli`)

## Task 2: Fix Failing Tests

**No failing tests found.** All 679 tests passed on initial run.

## Task 3: Code Quality Pass

**162 ruff errors found and fixed:**

| Category | Count | Examples |
|---|---|---|
| E501 (line too long) | ~25 | SQL strings, f-strings, prompt templates |
| E701 (multiple statements) | ~15 | One-liner if/return patterns in classifier.py |
| E402 (import order) | 3 | cli.py imports after constants |
| UP042 (str+Enum) | 4 | Converted to StrEnum in models.py |
| F841 (unused variables) | 3 | workflow_engine.py, chat.py, built_in_actions.py |
| B007 (unused loop vars) | 3 | dedup.py, prep.py, intent_router.py |
| E741 (ambiguous name) | 1 | `l` renamed to `line` in llm_router.py |
| Auto-fixed by ruff | ~102 | Import sorting, whitespace, etc. |

**82 files reformatted** by `ruff format`.

## Task 4: Environment Cleanup

- `.venv/` exists, already in `.gitignore` ✓
- No `requirements.txt` — `pyproject.toml` is sole source of truth ✓
- All core imports resolve (click, rich, yaml, pydantic) ✓
- Added `output/` and `*.png` to `.gitignore` (was untracked with 1500+ generated PNG frames)

## Task 5: CLAUDE.md Update

**Complete rewrite** from generic engineering rules to repo-specific documentation:
- Architecture overview with module descriptions
- All CLI commands documented with examples
- Data flow and key design decisions
- Test suite info and dependency table
- Agent behavior rules retained in condensed form

## Task 6: README.md Update

**Complete rewrite** from outdated placeholder to professional README:
- Accurate feature list reflecting current capabilities
- Installation and usage examples with real CLI commands
- Architecture diagram matching current module structure
- Related repos section

## Task 7: Claude Code Optimization

CLAUDE.md already contains all necessary Claude Code context:
- CLI entry points documented ✓
- Test commands documented ✓
- Config file locations documented ✓
- `.claude/` directory exists with settings ✓
- No claude-rules symlink (rules embedded in CLAUDE.md)

## Task 8: Test Coverage Analysis

**Modules with NO test coverage (6):**

| Module | Reason |
|---|---|
| `config.py` | Config loading, env vars — integration-style |
| `cli.py` | Click CLI — large, would need CLI runner tests |
| `models.py` | Pure dataclasses — low risk, high stability |
| `__main__.py` | Entry point wrapper — trivial |
| `overnight/cke_client.py` | External service client |
| `extraction/vault_writer.py` | Top-level extraction vault writer |

**Well-tested modules (all others):** query engine, index builder, workflow engine, intent router, LLM router, vault I/O, template manager, project resolver, task manager, all cleanup modules, doctor/integrity, freshness scanner, ingest router+classifier, overnight (state, safety, preflight, monitor, dedup, classifier), retrieval (engine, prep, rfp), all extraction/non_project modules, ops (database, registry).

## Issues Found (Not Fixed — Noted Only)

1. **Legacy code directories** (`src/_cli_v1_old/`, `src/agents/`, `src/core/`, `src/services/`) are not part of the main `corp_by_os` package. Candidates for cleanup/removal after confirming they're unused.

2. **No CI pipeline** — tests run locally only. Consider GitHub Actions with `pytest` + `ruff check`.

3. **Large untracked files** — `output/` directory contained 1500+ generated PNG frames from video extraction. Added to `.gitignore`.

4. **CRLF/LF inconsistency** — some files use LF, others CRLF. Consider adding `.gitattributes` with `* text=auto`.

---

## Final State

- **All 679 tests pass** (0 failures)
- **All ruff lint checks pass** (0 errors)
- **Master branch untouched** — all changes on `code-review-2026-03-15`
- **No functionality changed** — only quality, formatting, and documentation improvements
