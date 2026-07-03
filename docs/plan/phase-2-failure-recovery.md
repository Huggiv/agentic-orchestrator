# Phase 2: Failed Step Re-execution

## Objective

Allow safe, targeted recovery from failed orchestration steps.

## Scope

- Retry failed step only
- Retry from failed step onward
- Input override before retry
- Retry lineage and audit visibility

## Task List

| ID | Task | Area | Dependency | Estimate | Acceptance Criteria |
|---|---|---|---|---|---|
| P2-01 | Create retry request schema (`retry_mode`, `start_step`, `override_inputs`, `fallback_agent`) | Backend | P1-01 | M | Schema validation rejects invalid retry requests |
| P2-02 | Add endpoint for retrying failed step only | Backend | P2-01 | M | Failed step can be rerun independently |
| P2-03 | Add endpoint for retry-from-step behavior for dependent tail steps | Backend | P2-01 | M | Downstream steps rerun in correct order |
| P2-04 | Add idempotency protection for side-effect steps (commit/push/pr) | Backend | P2-02/P2-03 | L | Duplicate side effects prevented |
| P2-05 | Persist retry lineage (`parent_job_id`, `attempt_no`, `retry_reason`) | Backend | P2-01 | S | History can link retries to original run |
| P2-06 | Add failure insight payload with actionable guidance | Backend | P2-02 | M | UI receives error class and suggested action |
| P2-07 | Add retry controls to failed step UI | Frontend | P2-02/P2-03 | M | User can run retry actions directly |
| P2-08 | Show retry lineage badges and filters in history | Frontend | P2-05 | S | Retry history is visible and filterable |
| P2-09 | Add backend tests for retry semantics and idempotency | Tests | P2-04 | M | Retry logic tested for safe replay |
| P2-10 | Add frontend tests for retry workflows | Tests | P2-07/P2-08 | S | UI actions and state transitions validated |

## Risks and Mitigations

- Risk: Partial replay causes inconsistent artifacts.
- Mitigation: Use dependency-aware replay graph and step preconditions.

- Risk: Users trigger retries without understanding failure reason.
- Mitigation: Require failure insight to render before retry action.

## Exit Criteria

- Teams can recover from failures without full rerun and without duplicate side effects.
