# OTF Agentic Orchestrator

Agentic orchestration tool with:
- Python backend (FastAPI)
- React frontend (Vite)
- Modern Jira table UI (AG Grid)
- Jira-first workflow (on-prem Jira supported)
- Feature branch + commit + validation + PR automation

## Project Structure

- `backend/` FastAPI APIs and orchestration services
- `frontend/` React UI dashboard

## Implemented Flow (v0)

1. Fetch Jira issues from on-prem Jira.
2. Select Jira ticket from UI.
3. Provide target repository (`owner/repo` or clone URL) in UI.
4. Trigger orchestration API.
5. Clone repository into a temporary workspace.
6. Read Jira description and DoD for implementation context.
7. Generate agentic implementation notes via Copilot CLI.
8. Create feature branch, commit, push, and open PR.
9. Show live progress + estimated token/cost usage in UI.

## UI Behavior

- Jira Issues are displayed in a modern AG Grid table.
- Latest 10 Jira issues are loaded.
- Jira IDs are clickable and open the corresponding Jira issue page.
- Trigger form includes repository input.
- Run panel shows live step progress and estimated token/cost usage.

## Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
uvicorn app.main:app --reload --port 8015
```

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on `http://localhost:5175` and proxies API requests to backend `http://localhost:8015`.

## Docker Setup

Use Docker Compose to run backend and frontend together:

1. Create a `.env` file at the project root (same level as `docker-compose.yml`) with your Jira and GitHub credentials.
2. Start both services:

```bash
docker compose up --build
```

The root `.env` file is mounted into both containers at `/app/.env`.
Frontend container proxy is wired to backend service using `VITE_PROXY_TARGET=http://backend:8015`.
Backend clones the requested repository into a temporary directory for each run.

Services:
- Frontend: `http://localhost:5175`
- Backend: `http://localhost:8015`

To stop services:

```bash
docker compose down
```

## API Endpoints

- `GET /health`
- `GET /api/jira/issues?max_results=25`
- `POST /api/orchestrate` (returns `job_id`)
- `GET /api/orchestrate/{job_id}` (poll status + progress + result)
- `GET /api/orchestrate/history?limit=20` (persisted run history)

Default Jira issues filter (when `jql` is not passed):
- `project = "OTF" AND status != "DONE" ORDER BY updated DESC`

### Sample Orchestration Request

```json
{
  "jira_ticket_id": "PROJ-123",
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

## Important Notes

- Orchestration uses GitHub CLI (`gh`) + standalone Copilot CLI (`copilot`) with `GITHUB_TOKEN`.
- Branching, checkout, commit, push, and PR creation are automated by backend orchestration flow inside temp clone workspace.
- Ensure Copilot CLI is installed in the backend runtime: `npm install -g @github/copilot`.
- For headless Copilot execution, prefer `COPILOT_GITHUB_TOKEN` with the `Copilot Requests` permission. The backend falls back to `gh auth token` before reusing `GITHUB_TOKEN`.
- Orchestration run history is persisted in SQLite (`OTF_HISTORY_DB_PATH`, default `/tmp/otf-orch-history.db`).
- For on-prem Jira with self-signed certificates, set `JIRA_VERIFY_SSL=false`.

## Next Suggested Enhancements

- Integrate deeper coding-agent execution telemetry in orchestration history.
