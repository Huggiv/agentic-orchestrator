---
description: 'Python and FastAPI conventions for backend implementation in this repository.'
applyTo: 'backend/**/*.py'
---

# Python FastAPI Instructions

## Conventions
- Use explicit typing on public functions and router handlers.
- Keep request validation in pydantic models and route signatures.
- Return consistent JSON response shapes from routers.
- Keep orchestration and integration logic out of router handlers when possible.

## Testing
- Add pytest coverage in backend/tests for changed behavior.
- Prefer focused unit tests for service and orchestration logic.
- Include at least one failure-path test for external integration boundaries.

## Safety
- Do not log secrets, PATs, or token-like values.
- Validate user inputs before passing values to shell, git, or network operations.
