# Phase 1: Interactive Agentic Workflow Execution

## Objective

Enable live workflow monitoring and user controls during orchestration execution.

## Scope

- Real-time step timeline
- Pause and resume controls
- Approval checkpoint support
- Per-step logs and control flags

## Task List

| ID | Task | Area | Dependency | Estimate | Acceptance Criteria |
|---|---|---|---|---|---|
| P1-01 | Introduce canonical step states: pending/running/success/failed/skipped/paused/blocked_approval | Backend | P0-02 | M | Status endpoint includes normalized state per step |
| P1-02 | Extend job status payload with `current_step`, `next_step`, `allowed_actions` | Backend | P1-01 | M | Frontend can reliably render controls |
| P1-03 | Add `POST /api/orchestrate/{job_id}/pause` and `POST /api/orchestrate/{job_id}/resume` | Backend | P1-02 | M | State transitions validated and persisted |
| P1-04 | Add checkpoint behavior that blocks execution until approval action | Backend | P1-01 | L | Job can wait and continue based on user action |
| P1-05 | Persist detailed step logs to history store | Backend | P1-01 | M | Step-level logs visible in history/status |
| P1-06 | Build live timeline component updates in executing view | Frontend | P1-02 | M | Timeline reflects real-time transitions |
| P1-07 | Add pause/resume control buttons with state guards | Frontend | P1-03 | S | Controls shown only when actions are allowed |
| P1-08 | Add approval UI for blocked checkpoints | Frontend | P1-04 | M | Approve/reject actions unblock workflow |
| P1-09 | Add backend tests for transition safety and checkpoint gating | Tests | P1-03/P1-04 | M | Transition and gate tests pass |
| P1-10 | Add frontend tests for timeline rendering and control states | Tests | P1-06/P1-07/P1-08 | S | UI behavior validated for all key states |

## Risks and Mitigations

- Risk: Race conditions during pause/resume.
- Mitigation: Use explicit state lock and idempotent transition checks.

- Risk: UI drift from backend state model.
- Mitigation: Keep a shared status contract and add contract tests.

## Exit Criteria

- Users can observe, pause, resume, and approve interactive checkpoints without losing job integrity.
