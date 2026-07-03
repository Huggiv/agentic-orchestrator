# Project: Agentic Orchestrator

## Overview
Agentic Orchestrator automates Jira-driven software delivery with a FastAPI backend and a React (Vite) frontend. It manages issue intake, orchestration execution, and operational history.

## Tech Stack
- Language: Python 3.11+, JavaScript (React)
- Backend Framework: FastAPI
- Frontend Framework: React + Vite
- Package Managers: pip and npm
- Infrastructure: Docker Compose

## Code Standards
- Keep changes minimal and scoped to the feature or fix.
- Follow existing project structure and naming.
- Add or update tests for backend behavior changes in backend/tests.
- Prefer clear, explicit error handling for API and orchestration flows.

## Architecture Notes
- Backend code lives in backend/app with router modules in backend/app/routers.
- Jira integration lives under backend/app/jira.
- Orchestration logic is centralized in backend/app/orchestration.py.
- Frontend UI and service calls are in frontend/src.

## Development Workflow
1. Read README.md and inspect affected backend/frontend modules.
2. Implement focused changes and preserve API contracts unless requested.
3. Run backend tests and relevant frontend checks before finalizing.

## Important Patterns
- Maintain stable response formats for frontend compatibility.
- Keep long-running orchestration steps observable through status/history endpoints.
- Prefer configuration through existing env vars and config files.

## Do Not
- Do not introduce broad refactors in feature-specific tasks.
- Do not commit secrets or tokens.
- Do not break existing route paths without migration guidance.
