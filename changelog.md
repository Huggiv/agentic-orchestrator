# Changelog

All notable changes to this project are documented in this file.

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
