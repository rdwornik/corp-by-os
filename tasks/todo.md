# Week 2 — Folder-as-Package Ingest + LLM Fallback

## Implementation Plan

- [x] 1. Add InboxItem dataclass, rewrite scan_inbox() to return list[InboxItem]
- [x] 2. Add PackageIngestResult + ingest_folder() pipeline
- [x] 3. Update ingest_all() return type → tuple[file_results, package_results]
- [x] 4. Update CLI ingest command for two-table output (packages + files)
- [x] 5. Create llm_classifier.py (classify_file_llm, classify_quarantined_batch)
- [x] 6. Add CLI classify command (--model, --budget, --dry-run)
- [x] 7. Note TODOs for corp-os-meta fixes (6b, 6c)
- [ ] 8. Write tests for folder ingest + LLM classifier
- [ ] 9. Run full test suite, fix any failures
- [ ] 10. Commit

## Phase 1 Warnings (cross-repo TODOs)

- [x] 6a. ops.db path tracking — fixed (update_asset_path added)
- [ ] 6b. Unknown taxonomy terms — add WMS, Platform, Control Tower to
      products.yaml in corp-os-meta (separate repo, not accessible here)
- [ ] 6c. date field required — make `date` optional in corp-os-meta
      NoteFrontmatter (separate repo, not accessible here)
