# Step 3 — Chat Router

## Implementation Plan

- [x] 1. intent_router.py — keyword matching with trigger phrases + Polish stem prefix matching + parameter extraction
- [x] 2. llm_router.py — Gemini Flash structured classification via google.genai
- [x] 3. chat.py — Rich terminal loop with preview + confirm + execute
- [x] 4. cli.py — add `corp chat [--no-llm]` command
- [x] 5. pyproject.toml — google-genai optional dep, version 0.3.0
- [x] 6. .env — GEMINI_API_KEY, GEMINI_MODEL, LLM_DAILY_CAP
- [x] 7. test_intent_router.py — 40 tests (keyword matching, Polish/English, dates, products, priorities, stem matching)
- [x] 8. test_llm_router.py — 13 tests (JSON parsing, usage tracking, daily cap, mocked Gemini)
- [x] 9. test_chat.py — 10 tests (special commands, quit, workflow execution, loop behavior)
- [x] 10. All tests pass: 188/188 (117 old + 71 new)
- [ ] 11. Commit, merge, tag v0.3.0
