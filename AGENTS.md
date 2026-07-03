# AGENTS System Context

This file provides baseline behavior for CLI-style agent workflows in this repository.

## Repository Context
- Project: Agentic Orchestrator
- Domain: Jira-driven orchestration automation
- Backend: FastAPI (backend/app)
- Frontend: React + Vite (frontend/src)

## Working Rules
- Read existing code and tests before implementing changes.
- Keep diffs minimal and avoid unrelated refactors.
- Preserve public API behavior unless the task explicitly requires a change.
- Add tests for behavior changes where practical.
- Never add secrets to source control.

## Validation Checklist
1. Backend tests pass for changed areas.
2. API contracts remain compatible with frontend usage.
3. New files follow repository naming and structure conventions.
4. Documentation is updated when behavior or setup changes.

For richer Copilot context and project conventions, also refer to .github/copilot-instructions.md.
