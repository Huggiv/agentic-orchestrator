# Changelog

All notable changes to this project are documented in this file.

## [1.0.2] - 2026-06-26

### Added

- Chat API endpoint to cancel chat-triggered jobs directly: POST /api/chat/cancel/{job_id}.
- Chat inline job controls to cancel associated queued or running workflows.
- Manual model refresh controls in Run, Bulk Trigger modal, and floating Chat composer.
- Model service force-refresh capability for reloading backend model inventory without page reload.

### Changed

- Chat no-ticket requests now use backend LLM responses for concise actionable guidance in both standard and streaming chat flows.
- Jira bulk selection table now supports internal vertical scrolling with sticky header for easier multi-ticket selection.
- Model loading flow now uses stronger fallback behavior and shared loading/error handling.

### Fixed

- Model dropdowns not populating reliably after startup or stale cache scenarios.
- Chat experience when prompts contain no Jira ticket keys now returns useful assistant output instead of static guidance.
- Added backend test coverage for no-ticket LLM path and chat cancellation guard rails.

## [1.0.0] - 2026-06-24

### Added

- Copilot custom agent installation in backend container from backend agents directory.
- Backend API endpoint to list available agents: GET /api/agents.
- Orchestration request support for explicit selected_agent.
- Shared step visualization component used consistently across Run, Executing, and History views.
- Persistent running jobs recovery in frontend by rehydrating queued/running jobs from history.
- History card usage table with highlighted Changes and Cost rows.
- History card expandable sections for Changes and Usage, plus Artifacts.

### Changed

- Default orchestration agent selection now uses SWE with optional Jira-driven specialist matching or explicit user selection.
- Commit step classification now reports success when commits already exist ahead of base branch.
- Run flow now opens directly in Executing tab after trigger.
- History card layout improved: stronger View PR callout, trigger time placement, and duration badge formatting.

### Fixed

- Executing tab intermittently missing running jobs after page reload.
- Commit Changes step incorrectly marked skipped in cases where commits were already present.
