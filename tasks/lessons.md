# Lessons Learned — corp-by-os

*Updated by Claude Code after corrections. Review at session start.*

## Windows subprocess encoding (2026-03-09)

**Problem:** `subprocess.run(text=True)` defaults to `cp1252` on Windows. Agent CLIs using Rich output UTF-8 box-drawing chars (┌──) which can't be decoded → `UnicodeDecodeError` → stdout/stderr become `None` → step appears to fail silently.

**Fix:** Always use `encoding="utf-8", errors="replace"` in all `subprocess.run` calls.

**Rule:** Every `subprocess.run` in this codebase MUST specify `encoding="utf-8"` when using `text=True`.

## Folder naming alignment (2026-03-09)

**Problem:** `com new` creates folders as `{client}_{product}` (verbatim, preserving case). Our `create_vault_skeleton` was using `_slugify()` which lowercases AND replaces spaces/hyphens, producing different project_ids.

**Fix:** Use `f"{client}_{product}".lower()` to match `com`'s `{client}_{product}` pattern exactly, just lowercased for vault convention.

**Rule:** Always match the upstream tool's naming convention, then apply our normalization on top.
