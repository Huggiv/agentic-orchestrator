---
title: Copilot-Like Chatbot Replacement Specification
version: 1.0
date_created: 2026-07-03
last_updated: 2026-07-03
owner: Agentic Orchestrator Engineering
tags: [design, app, chatbot, jira, orchestration]
---

# Introduction

This specification defines the full replacement of the existing chatbot implementation with a Copilot-like conversational assistant for Agentic Orchestrator. The new chatbot must support stateful sessions, Jira-aware grooming when ticket IDs are present, selected-LLM-assisted refinement, and explicit user confirmation before triggering an agentic workflow.

## 1. Purpose & Scope

Purpose:
- Replace the current chatbot implementation end-to-end with a new architecture and user experience that is similar to GitHub Copilot chat sessions.

Scope:
- Backend chat session APIs, message lifecycle, Jira enrichment, LLM prompt orchestration, grooming outputs, and orchestration trigger handoff.
- Frontend chat session UX, conversation persistence, interactive prompts, and trigger confirmation flow.
- Decommissioning and removal of current chatbot code paths and UI interactions.

Audience:
- Backend engineers, frontend engineers, QA engineers, and product owners.

Assumptions:
- Existing Jira integration and orchestration APIs remain available.
- Existing model selection support is available and can be reused.
- Existing authentication and authorization boundaries remain unchanged.

Out of Scope:
- Major changes to Jira client authentication flow.
- Replacing orchestration engine internals unrelated to trigger handoff.

## 2. Definitions

- Chat Session: A persistent, ordered conversation container with metadata and message history.
- Conversation Turn: One user message and one assistant response pair.
- Grooming: Requirement refinement process that converts raw feature requests into structured implementation-ready inputs.
- LLM: Large Language Model used to generate chat and grooming responses.
- Jira ID: Ticket identifier matching pattern `[A-Z][A-Z0-9_]+-[0-9]+`.
- Jira Enrichment: Fetching Jira issue details from backend and adding them to LLM context.
- Trigger Confirmation: Explicit user action approving workflow launch.
- Agentic Workflow: Existing orchestration process started via orchestration API.

## 3. Requirements, Constraints & Guidelines

- **REQ-001**: The system shall remove and replace the current chatbot implementation (backend and frontend) with the new Copilot-like chatbot implementation.
- **REQ-002**: The system shall support multi-session chat where each session has isolated context, title, and ordered message history.
- **REQ-003**: The system shall persist session context and message history so the user can close and return to the same session without losing state.
- **REQ-004**: The chatbot shall support interactive conversational behavior similar to Copilot sessions, including follow-up questioning and progressive refinement.
- **REQ-005**: When a user message contains one or more Jira IDs, the backend shall fetch Jira details and inject normalized issue context into the LLM prompt.
- **REQ-006**: The chatbot shall groom feature requests using the user-selected LLM model and return structured grooming output.
- **REQ-007**: The chatbot shall ask for explicit user confirmation before triggering any agentic workflow.
- **REQ-008**: The chatbot shall generate a run-ready orchestration payload from grooming output and Jira context.
- **REQ-009**: The chatbot shall allow user edits to generated payload fields (agent, repository, base branch, reviewer, change plan) before trigger.
- **REQ-010**: The system shall preserve backward compatibility for non-chat orchestration endpoints and existing history endpoints.

- **SEC-001**: The system shall never persist secrets or access tokens in chat messages, session metadata, or logs.
- **SEC-002**: The system shall redact sensitive values from error messages shown to users.
- **SEC-003**: Authorization checks for chat/session endpoints shall follow existing application auth controls.

- **CON-001**: Jira enrichment must be backend-only; frontend must not directly call Jira APIs for chat grooming.
- **CON-002**: Chat session context storage must be deterministic and replayable by session ID.
- **CON-003**: Trigger execution is forbidden without explicit confirmation event in the same active session.
- **CON-004**: Existing route contracts unrelated to chat must remain unchanged.

- **GUD-001**: Use concise assistant responses with optional expandable structured blocks.
- **GUD-002**: Prefer explicit schema fields over free-form markdown for grooming outputs.
- **GUD-003**: Use idempotency keys for trigger calls created from chat to avoid duplicate workflow launches.

- **PAT-001**: Implement chat with session-centric state machine (Draft -> Grooming -> ReadyToTrigger -> Triggered -> Closed).
- **PAT-002**: Use append-only message log with immutable message IDs and timestamps.

## 4. Interfaces & Data Contracts

### 4.1 Backend API Endpoints (Target)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/chat/sessions` | POST | Create a chat session |
| `/api/chat/sessions` | GET | List chat sessions |
| `/api/chat/sessions/{session_id}` | GET | Get session metadata + message summary |
| `/api/chat/sessions/{session_id}/messages` | GET | Get full ordered messages |
| `/api/chat/sessions/{session_id}/messages` | POST | Send user message and receive assistant response |
| `/api/chat/sessions/{session_id}/prepare-trigger` | POST | Build editable orchestration payload |
| `/api/chat/sessions/{session_id}/confirm-trigger` | POST | Confirm and trigger orchestration job |
| `/api/chat/sessions/{session_id}` | DELETE | Archive or close session |

### 4.1.1 Legacy Endpoint Deprecation Matrix

| Legacy Endpoint | Action | Replacement | Deprecation Window |
|---|---|---|---|
| `/api/chat/message` | Hard-deprecate and remove | `/api/chat/sessions/{session_id}/messages` | 1 release |
| `/api/chat/message/stream` | Hard-deprecate and remove | `/api/chat/sessions/{session_id}/messages` (streaming extension optional) | 1 release |
| `/api/chat/grooming/assign` | Replace | `/api/chat/sessions/{session_id}/confirm-trigger` | 1 release |

If compatibility wrappers are used during deprecation window, wrappers must emit deprecation metadata in response headers and logs.

### 4.2 Message Request Contract

```json
{
  "message": "Need to groom AGENT_FLOW-245 and AGENT_FLOW-246 for rollout",
  "model": "gpt-5.3-codex",
  "mode": "interactive",
  "client_context": {
    "active_repository": "owner/repo",
    "active_branch": "main"
  }
}
```

### 4.3 Message Response Contract

```json
{
  "session_id": "uuid",
  "assistant_message": {
    "id": "msg_uuid",
    "role": "assistant",
    "content": "I fetched Jira details and drafted grooming output. Please review.",
    "created_at": "2026-07-03T12:00:00Z"
  },
  "jira_enrichment": {
    "ticket_ids": ["AGENT_FLOW-245", "AGENT_FLOW-246"],
    "fetched": true,
    "missing": []
  },
  "grooming": {
    "problem": "...",
    "user_impact": "...",
    "goals": ["..."],
    "constraints": ["..."],
    "acceptance_criteria": ["..."]
  },
  "trigger_state": {
    "status": "awaiting_confirmation",
    "recommendation": "ready"
  }
}
```

### 4.4 Prepare Trigger Contract

```json
{
  "selected_agent": "SWE",
  "repository": "owner/repo",
  "base_branch": "main",
  "reviewer": "teammate",
  "commit_message": "feat(agent_flow-245): implement groomed scope",
  "change_plan": [
    "Implement backend updates",
    "Implement frontend updates",
    "Validate build and lint"
  ]
}
```

### 4.5 Confirm Trigger Response Contract

```json
{
  "session_id": "uuid",
  "trigger_confirmation_id": "uuid",
  "job": {
    "job_id": "uuid",
    "status": "queued"
  }
}
```

### 4.6 Error Response Contract

All chat/session endpoints shall return a normalized error payload.

```json
{
  "error": {
    "code": "TRIGGER_CONFIRMATION_REQUIRED",
    "message": "Trigger confirmation is required before workflow launch.",
    "request_id": "uuid",
    "details": {
      "session_id": "uuid",
      "state": "ReadyToTrigger"
    }
  }
}
```

Required error codes:
- `SESSION_NOT_FOUND`
- `SESSION_CLOSED`
- `JIRA_ENRICHMENT_PARTIAL`
- `JIRA_ENRICHMENT_FAILED`
- `GROOMING_SCHEMA_INVALID`
- `TRIGGER_CONFIRMATION_REQUIRED`
- `TRIGGER_ALREADY_CONFIRMED`
- `UNAUTHORIZED`
- `FORBIDDEN`

### 4.7 Session State Machine Contract

| Current State | Event | Next State | Guard |
|---|---|---|---|
| Draft | UserMessageReceived | Grooming | Session is open |
| Grooming | GroomingComplete | ReadyToTrigger | Required schema fields are present |
| Grooming | NeedClarification | Grooming | Missing required schema fields |
| ReadyToTrigger | PrepareTrigger | ReadyToTrigger | Payload generated and editable |
| ReadyToTrigger | ConfirmTrigger | Triggered | Explicit confirmation event in active session |
| Triggered | ArchiveSession | Closed | Job linkage persisted |
| Any Open State | ArchiveSession | Closed | Session is open |

## 5. Acceptance Criteria

- **AC-001**: Given an existing session, when the user sends a follow-up message, then the assistant response shall incorporate prior session context without requiring the user to restate it.
- **AC-002**: Given a message containing one or more Jira IDs, when the backend processes the message, then Jira issue details shall be fetched and included in assistant reasoning context.
- **AC-003**: Given Jira enrichment succeeds, when grooming is generated, then response shall include structured fields: `problem`, `user_impact`, `goals`, `constraints`, `acceptance_criteria`.
- **AC-004**: Given grooming output is available, when the user requests workflow launch, then system shall first return editable orchestration payload and await explicit confirmation.
- **AC-005**: Given no confirmation event exists, when user or client attempts trigger execution, then system shall reject with validation error.
- **AC-006**: Given a confirmed trigger, when orchestration starts, then system shall return `job_id` and persist trigger linkage to the originating session.
- **AC-007**: Given the user refreshes or reopens UI, when they open a prior session, then full message history and trigger state shall be restored.
- **AC-008**: Given old chatbot routes/components exist, when migration is completed, then old implementation paths shall be removed or hard-deprecated with explicit compatibility wrappers.

## 6. Test Automation Strategy

- **Test Levels**: Unit, Integration, End-to-End
- **Frameworks**: Pytest (backend), Vitest + React Testing Library (frontend), Playwright (E2E)
- **Test Data Management**: Use deterministic fixtures for Jira responses and synthetic session IDs; isolate per test run and clean up session storage.
- **CI/CD Integration**: Run backend + frontend tests in GitHub Actions on pull request; block merge on failures.
- **Coverage Requirements**: Minimum 85% coverage for new chat/session modules and 100% for trigger confirmation guard logic.
- **Performance Testing**: Validate p95 response time for message endpoint under concurrent sessions; target <= 2.5s without Jira and <= 4.5s with Jira enrichment.

## 7. Rationale & Context

The current chatbot implementation has evolved incrementally and now mixes support chat, grooming flow, and trigger handoff behavior. A replacement specification is required to unify these behaviors into a coherent session-first model similar to Copilot chat. The new design improves user trust and productivity by preserving context, reducing repeated prompts, and enforcing explicit human confirmation before workflow automation.

Backend Jira enrichment ensures secure data access and consistent normalization before LLM usage. Structured grooming outputs improve determinism and make downstream orchestration payload generation auditable and editable.

## 8. Dependencies & External Integrations

### External Systems
- **EXT-001**: Jira API - Fetch issue metadata and detailed context for grooming.
- **EXT-002**: GitHub API/Repository access - Required by orchestration payload target repository details.

### Third-Party Services
- **SVC-001**: LLM inference service - Provide conversational responses and structured grooming output generation.

### Infrastructure Dependencies
- **INF-001**: Persistent chat session storage - Durable store for session metadata and message history.
- **INF-002**: Existing orchestration job persistence - Link job triggers back to chat sessions.

### Data Dependencies
- **DAT-001**: Jira issue payloads - Issue summary, description, status, and optional acceptance notes.

### Technology Platform Dependencies
- **PLT-001**: FastAPI backend runtime - Supports chat/session API contract and async operations.
- **PLT-002**: React frontend runtime - Supports session UI, message rendering, and interactive controls.

### Compliance Dependencies
- **COM-001**: Internal secret-handling policy - Prevent token leakage in user-visible chat logs.

**Note**: Dependencies are capability-focused and do not mandate specific package versions unless otherwise constrained by platform compatibility.

## 9. Examples & Edge Cases

```code
Example 1: Jira-aware grooming
Input message: "Please groom AGENT_FLOW-321 for next sprint"
Behavior:
1) Detect Jira ID AGENT_FLOW-321.
2) Fetch issue details from backend Jira service.
3) Ask LLM for structured grooming output.
4) Return summary + follow-up questions for missing fields.

Example 2: No Jira ID provided
Input message: "Need to improve release stability"
Behavior:
1) Do not call Jira.
2) Continue interactive grooming with clarifying questions.
3) Offer to attach Jira IDs before trigger.

Example 3: Explicit trigger confirmation required
Input: user clicks "Trigger Workflow" without confirming payload review
Behavior: API returns validation error and keeps state at awaiting_confirmation.

Example 4: Session context restoration
Input: user refreshes page and opens same session
Behavior: previous messages, structured grooming, recommended agent/template, and trigger state are restored.
```

## 10. Validation Criteria

- Chat session API supports create/list/get/archive with deterministic behavior.
- Message endpoint supports context continuity across at least 50 turns per session.
- Jira enrichment executes only when Jira IDs are present and handles partial fetch failures gracefully.
- Grooming output always serializes to the required schema or returns schema-validation error.
- Trigger confirmation guard blocks unconfirmed runs in all code paths.
- Frontend session UI supports session switching, context restore, and interactive follow-up.
- Migration checklist confirms legacy chatbot implementation is removed or hard-deprecated.

## 11. Migration & Rollout Plan

### 11.1 Migration Requirements

- **MIG-001**: The existing chatbot backend router and frontend components shall be replaced behind a feature flag `chatbot_v2_enabled`.
- **MIG-002**: During one release window, compatibility wrappers may map legacy requests to session APIs.
- **MIG-003**: All new chat records must be stored using session-centric schema; no dual-write to legacy schema after cutover.
- **MIG-004**: Trigger linkage from chat to orchestration job must be backfilled for in-flight sessions at migration time.
- **MIG-005**: Legacy routes must be removed after deprecation window and release notes must document removal.

### 11.2 Rollout Phases

1. Internal rollout with feature flag enabled for admin users only.
2. Canary rollout for 10% of authenticated users.
3. Full rollout when error budget and success metrics meet thresholds.
4. Legacy endpoint removal and compatibility wrapper shutdown.

### 11.3 Rollback Criteria

Rollback to prior chatbot implementation if any condition is met:
- p95 chat latency exceeds threshold by more than 30% for 30 minutes.
- Trigger-confirmation failures exceed 5% of attempts for 15 minutes.
- Session restore failure rate exceeds 1%.

### 11.4 Operational Metrics

- Session restoration success rate
- Jira enrichment success/partial/failure rates
- Grooming schema completeness rate
- Trigger confirmation to launch conversion rate
- Duplicate trigger prevention rate

## 12. Related Specifications / Further Reading

- [Agentic Orchestrator Project Summary](../docs/project-summary.md)
- [Phase 3 Grooming Chatbot Plan](../docs/plan/phase-3-grooming-chatbot.md)
- [Repository README](../README.md)