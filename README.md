# Agentic Orchestrator

Agentic Orchestrator automates Jira-driven implementation workflows using a FastAPI backend and a React (Vite) frontend.

## Table of Contents

- [Agentic Orchestrator](#agentic-orchestrator)
  - [Table of Contents](#table-of-contents)
  - [Overview](#overview)
  - [Architecture](#architecture)
  - [Project Structure](#project-structure)
  - [Prerequisites](#prerequisites)
  - [Configuration](#configuration)
  - [Run Locally](#run-locally)
    - [Backend](#backend)
    - [Frontend](#frontend)
  - [Run with Docker Compose](#run-with-docker-compose)
    - [Compose v1](#compose-v1)
    - [Compose v2](#compose-v2)
  - [API Reference](#api-reference)
    - [Health](#health)
    - [Jira](#jira)
    - [Orchestration](#orchestration)
  - [Execution Flow](#execution-flow)
  - [Operations and Troubleshooting](#operations-and-troubleshooting)
  - [Additional Documentation](#additional-documentation)

## Overview

This project provides:

- Jira-first orchestration flow (including on-prem Jira)
- Automated repository clone, branch, commit, push, and PR creation
- Copilot CLI-driven implementation notes and usage telemetry
- Live execution progress and persisted run history

## Architecture

- Backend: FastAPI service (`backend/`)
- Frontend: React + Vite dashboard (`frontend/`)
- Persistence: SQLite history store
- Integrations: Jira API, GitHub API, GitHub CLI (`gh`), Copilot CLI (`copilot`)

## Project Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── jira/
│   │   ├── routers/
│   │   ├── history_store.py
│   │   ├── main.py
│   │   └── orchestration.py
│   ├── config/
│   │   └── filters.yaml
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── FlowDiagram.jsx
│   │   └── styles.css
│   ├── Dockerfile
│   ├── package.json
│   └── vite.config.js
├── docker-compose.yml
└── README.md
```

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker + Docker Compose (v1 `docker-compose` or v2 `docker compose`)
- GitHub CLI (`gh`) for backend orchestration runtime
- Copilot CLI (`copilot`) for agentic implementation prompts

## Configuration

Create `.env` at repository root (same level as `docker-compose.yml`).

Core variables:

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | Yes | GitHub token used for git/PR operations |
| `COPILOT_GITHUB_TOKEN` | Recommended | Token with `Copilot Requests` permission |
| `JIRA_URL` | Yes | Jira base URL |
| `JIRA_PAT` | Yes | Jira PAT for API access |
| `JIRA_VERIFY_SSL` | No | Set `false` for self-signed on-prem certs |
| `AGENT_FLOW_REPO_BASE_DIR` | No | Temp clone workspace path |
| `AGENT_FLOW_HISTORY_DB_PATH` | No | SQLite db path for orchestration history |

## Run Locally

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8015
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

- Frontend: `http://localhost:5175`
- Backend: `http://localhost:8015`

## Run with Docker Compose

Use one of the following based on your installation.

### Compose v1

```bash
docker-compose up --build
```

Stop:

```bash
docker-compose down
```

### Compose v2

```bash
docker compose up --build
```

Stop:

```bash
docker compose down
```

Runtime notes:

- Root `.env` is mounted to `/app/.env` in both containers.
- Frontend proxies to backend using `VITE_PROXY_TARGET=http://backend:8015`.
- Backend clones target repositories into temp workspace for each run.
- Host bind mounts default to `${HOME}/agent_flow-tmp-repos`, `${HOME}/agent_flow-history`, and `${HOME}/.copilot/agentic-orch`.
- You can override host paths in `.env` with `AGENT_FLOW_HOST_REPO_DIR`, `AGENT_FLOW_HOST_HISTORY_DIR`, and `AGENT_FLOW_HOST_COPILOT_STATE_DIR`.

Before first run, create the host mount directories:

```bash
mkdir -p "$HOME/agent_flow-tmp-repos" "$HOME/agent_flow-history" "$HOME/.copilot/agentic-orch"
```

## API Reference

### Health

- `GET /health`

### Jira

- `GET /api/jira/issues?max_results=25`
- `GET /api/jira/issues/{issue_key}`

Default JQL when `jql` is omitted:

```text
project = "AGENT_FLOW" AND status != "DONE" ORDER BY updated DESC
```

### Orchestration

- `GET /api/agents`
- `POST /api/orchestrate` (returns `job_id`)
- `GET /api/orchestrate/{job_id}`
- `GET /api/orchestrate/history?limit=20`

Sample request:

```json
{
  "jira_ticket_id": "PROJ-123",
  "selected_agent": "SWE",
  "repository": "owner/repository",
  "base_branch": "development",
  "reviewer": "teammate-user",
  "commit_message": "feat(proj-123): automated implementation",
  "change_plan": [
    "Analyze impacted files",
    "Apply code changes in small commits",
    "Run tests and lint checks"
  ]
}
```

## Execution Flow

1. Fetch Jira issues and select ticket.
2. Provide repository (`owner/repo` or clone URL).
3. Trigger orchestration job.
4. Clone repository and read Jira context + DoD.
5. Generate implementation guidance via Copilot CLI.
6. Create feature branch, commit, push, and open PR.
7. Track live progress and inspect persisted run history.

## Operations and Troubleshooting

- Use a dedicated `COPILOT_GITHUB_TOKEN` for stable headless Copilot execution.
- Backend falls back to `gh auth token` before reusing `GITHUB_TOKEN`.
- History persistence uses SQLite (`AGENT_FLOW_HISTORY_DB_PATH`).
- If Docker Compose v2 is unavailable, use `docker-compose` commands.

## Additional Documentation

Generated architecture and project documentation:

- `changelog.md`
- `docs/releases/v1.0.0.md`
- `docs/project-summary.md`
- `docs/diagrams/high-level-architecture.puml`
- `docs/diagrams/processing-pipeline.puml`
- `docs/diagrams/component-relationships.puml`
- `docs/diagrams/deployment-infrastructure.puml`
