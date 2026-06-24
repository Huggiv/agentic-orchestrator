from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException, Query

from app.history_store import get_history_store
from app.orchestration import OrchestrationError, run_orchestration
from app.jira import service as jira_service

router = APIRouter(prefix="/api", tags=["orchestrate"])


class OrchestrateRequest(BaseModel):
    jira_ticket_id: str = Field(min_length=2)
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    reviewer: Optional[str] = None
    selected_agent: Optional[str] = None
    selected_model: Optional[str] = None
    commit_message: str = Field(min_length=3)
    change_plan: list[str] = Field(default_factory=list)


def _extract_agent_name(agent_file: Path) -> str:
    try:
        content = agent_file.read_text(encoding="utf-8")
    except OSError:
        return agent_file.stem

    match = re.search(r"^name:\s*['\"]?([^'\"\n]+)['\"]?\s*$", content, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return agent_file.stem


def _discover_agent_names() -> list[str]:
    candidate_dirs: list[Path] = []

    global_dir = os.environ.get("COPILOT_CLI_GLOBAL_AGENTS_DIR")
    if global_dir:
        candidate_dirs.append(Path(global_dir))

    candidate_dirs.extend(
        [
            Path("/app/agents"),
            Path(__file__).resolve().parents[2] / "agents",
        ]
    )

    agent_files: list[Path] = []
    for directory in candidate_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        agent_files.extend(sorted(directory.glob("*.md")))
        agent_files.extend(sorted(directory.glob("*.agent.md")))

    names = {"SWE"}
    for file in agent_files:
        names.add(_extract_agent_name(file))

    return sorted(name for name in names if name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_job(job_id: str, payload: OrchestrateRequest) -> None:
    history_store = get_history_store()
    history_store.set_job_fields(job_id, status="running", started_at=_now())

    def progress_callback(event: dict) -> None:
        history_store.append_progress(job_id, event)

    try:
        result = run_orchestration(
            jira_ticket_id=payload.jira_ticket_id,
            repository=payload.repository,
            base_branch=payload.base_branch,
            reviewer=payload.reviewer,
            selected_agent=payload.selected_agent,
            selected_model=payload.selected_model,
            commit_message=payload.commit_message,
            change_plan=payload.change_plan,
            progress_callback=progress_callback,
        )
        history_store.set_job_fields(
            job_id,
            status="success",
            finished_at=_now(),
            result=result,
            error=None,
        )
    except OrchestrationError as e:
        history_store.set_job_fields(job_id, status="failed", finished_at=_now(), error=str(e))
    except Exception as e:  # noqa: BLE001
        history_store.set_job_fields(
            job_id,
            status="failed",
            finished_at=_now(),
            error=f"Unexpected error: {e}",
        )


@router.post("/orchestrate")
def orchestrate(payload: OrchestrateRequest):
    job_id = str(uuid4())
    created_at = _now()
    
    # Fetch full Jira details
    jira_details = {}
    try:
        issue = jira_service.get_issue(payload.jira_ticket_id)
        jira_details = {
            "jira_title": issue.get("summary", ""),
            "jira_summary": issue.get("summary", ""),
            "jira_description": issue.get("description", ""),
            "jira_type": issue.get("type", ""),
        }
    except Exception:
        pass
    
    get_history_store().create_job(
        job_id=job_id,
        created_at=created_at,
        request_payload={
            "jira_ticket_id": payload.jira_ticket_id,
            "repository": payload.repository,
            "base_branch": payload.base_branch,
            "reviewer": payload.reviewer,
            "selected_agent": payload.selected_agent,
            "selected_model": payload.selected_model,
            "commit_message": payload.commit_message,
            "change_plan": payload.change_plan,
            "jira_url": os.environ.get("JIRA_URL"),
            **jira_details,
        },
    )

    worker = Thread(target=_run_job, args=(job_id, payload), daemon=True)
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@router.get("/orchestrate/history")
def orchestrate_history(
    limit: int = Query(default=20, ge=1, le=200),
    include_progress: bool = Query(default=True),
):
    items = get_history_store().list_jobs(limit=limit, include_progress=include_progress)
    return {"items": items}


@router.get("/agents")
def list_agents():
    return {"items": _discover_agent_names()}


@router.get("/orchestrate/{job_id}")
def orchestrate_status(job_id: str):
    job = get_history_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")
    return job
