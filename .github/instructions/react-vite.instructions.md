---
description: 'React and Vite conventions for frontend changes in this repository.'
applyTo: 'frontend/src/**/*.{js,jsx,css}'
---

# React Vite Instructions

## Conventions
- Keep components small and focused on one responsibility.
- Keep API calls in service modules under frontend/src/services when practical.
- Reuse existing UI patterns from current components before adding new patterns.
- Prefer readable state flow over deeply nested derived state.

## UX
- Preserve existing layout behavior for desktop and mobile.
- Keep loading and error states explicit for async interactions.
- Avoid introducing visual regressions in job history and orchestration status views.

## Quality
- Update or add tests if frontend test setup exists for changed behavior.
- Keep props and event contracts stable for shared components.
