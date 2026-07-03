---
name: 'release-readiness'
description: 'Runs a lightweight release readiness checklist for API, tests, docs, and operational safety before shipping.'
---

# Release Readiness

## Purpose
Provide a repeatable pre-release checklist for Agentic Orchestrator changes.

## Instructions
1. Confirm API behavior changes are intentional and documented.
2. Verify backend tests pass for changed modules.
3. Verify frontend build or checks pass for changed UI modules.
4. Confirm no secrets or credentials were introduced.
5. Confirm changelog or release notes are updated when needed.
6. Summarize release risk as low, medium, or high with reasoning.

## Assets
- README.md
- changelog.md
- backend/tests
- docs/releases
