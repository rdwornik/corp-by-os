# Step 2 — Workflow Engine + 8 Golden Workflows

## Implementation Plan

- [x] 1. models.py — add Workflow, WorkflowStep, StepResult, WorkflowResult, Task dataclasses
- [x] 2. workflow_engine.py — load, execute, preview, agent/vault/python step handlers
- [x] 3. built_in_actions.py — all python step handlers (10 registered actions)
- [x] 4. task_manager.py — add_task, list_tasks, complete_task, task_dashboard_md
- [x] 5. config/workflows.yaml — all 8 workflow definitions
- [x] 6. cli.py — corp run, corp task add/list/done, corp tasks
- [x] 7. test_workflow_engine.py — 26 tests (load, execute, dry-run, agent/python steps, helpers)
- [x] 8. test_task_manager.py — 18 tests (add, list, complete, filter, parse, dashboard)
- [x] 9. test_built_in_actions.py — 20 tests (registry, skeleton, attention, brief, archive, inbox, copy)
- [x] 10. All tests pass: 115/115 (44 old + 71 new)
- [ ] 11. Commit, merge, tag v0.2.0
