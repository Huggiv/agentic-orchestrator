# Phase 3: Chatbot Grooming and Flow Assignment

## Objective

Provide conversational feature grooming that creates a run-ready agentic execution plan.

## Scope

- Guided requirement collection
- Acceptance criteria generation
- Flow template recommendation
- One-click assignment to orchestration run

## Task List

| ID | Task | Area | Dependency | Estimate | Acceptance Criteria |
|---|---|---|---|---|---|
| P3-01 | Define grooming schema: problem, user impact, goals, constraints, acceptance criteria | Backend | P0-04 | M | Grooming response is structured and complete |
| P3-02 | Add grooming mode to chat endpoints | Backend | P3-01 | M | Chat can switch support mode vs grooming mode |
| P3-03 | Add follow-up question engine for missing requirements | Backend | P3-01 | M | Chat asks targeted clarification prompts |
| P3-04 | Add flow template recommendation logic (feature/bugfix/devops) | Backend | P3-01 | M | Recommended flow returned with rationale |
| P3-05 | Add endpoint to transform grooming output into orchestration payload | Backend | P3-02/P3-04 | M | Payload is directly executable by orchestrate route |
| P3-06 | Add Jira field prefill generation (summary/description/criteria/labels) | Backend | P3-05 | M | Jira draft content generated from grooming output |
| P3-07 | Add guided grooming UI in chat console | Frontend | P3-02/P3-03 | M | User can complete grooming through conversational flow |
| P3-08 | Add review-and-assign UI to choose agent and start run | Frontend | P3-05/P3-07 | M | One-click assignment starts orchestration |
| P3-09 | Add backend tests for schema completeness and recommendation determinism | Tests | P3-04 | S | Recommendation behavior remains stable |
| P3-10 | Add frontend tests for grooming-to-assignment journey | Tests | P3-07/P3-08 | S | End-to-end grooming UX validated |

## Risks and Mitigations

- Risk: Low-quality prompts produce low-quality plans.
- Mitigation: Add required-field checks and confidence gating before assignment.

- Risk: Overly verbose chatbot output reduces usability.
- Mitigation: Enforce concise, structured response format for grooming summaries.

## Exit Criteria

- User can groom a new feature idea and launch a flow-ready orchestration request in one guided journey.
