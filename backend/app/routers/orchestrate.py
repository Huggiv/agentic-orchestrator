from __future__ import annotations

import os
from datetime import datetime, timezone
from threading import Thread
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException, Query

from app.history_store import get_history_store
from app.orchestration import OrchestrationError, run_orchestration

router = APIRouter(prefix="/api", tags=["orchestrate"])


class OrchestrateRequest(BaseModel):
    jira_ticket_id: str = Field(min_length=2)
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    reviewer: Optional[str] = None
    commit_message: str = Field(min_length=3)
    change_plan: list[str] = Field(default_factory=list)


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
    get_history_store().create_job(
        job_id=job_id,
        created_at=created_at,
        request_payload={
            "jira_ticket_id": payload.jira_ticket_id,
            "repository": payload.repository,
            "base_branch": payload.base_branch,
            "reviewer": payload.reviewer,
            "commit_message": payload.commit_message,
            "change_plan": payload.change_plan,
            "jira_url": os.environ.get("JIRA_URL"),
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


@router.get("/orchestrate/{job_id}")
def orchestrate_status(job_id: str):
    job = get_history_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")
    return job
