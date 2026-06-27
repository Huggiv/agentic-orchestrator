from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, Query

from app.history_store import get_history_store
from app.orchestration import CancellationToken, OrchestrationCancelled, OrchestrationError, run_orchestration
from app.jira import service as jira_service
from app.routers.auth import require_admin, require_run_permission

router = APIRouter(prefix="/api", tags=["orchestrate"])
_JOB_CANCEL_TOKENS: dict[str, CancellationToken] = {}
_JOB_CANCEL_LOCK = Lock()


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


def _workspace_dir_for_job(job_id: str) -> str:
    base_dir = Path(os.environ.get("AGENT_FLOW_REPO_BASE_DIR", "/tmp/agent_flow-tmp-repos"))
    return str(base_dir / f"agent_flow-agentic-{job_id[:8]}")


def _build_result_fallback(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}

    # Keep critical panels (PR/artifacts/steps/usage) available in history even when
    # full payload serialization fails.
    return {
        "branch_name": result.get("branch_name"),
        "pull_request_url": result.get("pull_request_url"),
        "workspace_dir": result.get("workspace_dir"),
        "steps": result.get("steps") or [],
        "selected_agent": result.get("selected_agent"),
        "artifacts": result.get("artifacts") or [],
        "usage": result.get("usage") or {},
        "warnings": [
            "Full orchestration result payload could not be persisted; stored fallback fields.",
        ],
    }


def _fetch_jira_details(jira_ticket_id: str) -> dict:
    try:
        issue = jira_service.get_issue(jira_ticket_id)
    except Exception:
        return {}

    return {
        "jira_title": issue.get("summary", ""),
        "jira_summary": issue.get("summary", ""),
        "jira_description": issue.get("description", ""),
        "jira_type": issue.get("type", ""),
    }


def _run_job(job_id: str, payload: OrchestrateRequest) -> None:
    with _JOB_CANCEL_LOCK:
        cancel_token = _JOB_CANCEL_TOKENS.get(job_id)
    if cancel_token is None:
        return

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
            cancellation_token=cancel_token,
            run_id=f"agent_flow-agentic-{job_id[:8]}",
        )
    except OrchestrationCancelled:
        history_store.set_job_fields(
            job_id,
            status="cancelled",
            finished_at=_now(),
            error="Cancelled by user request",
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
    else:
        try:
            history_store.set_job_fields(
                job_id,
                status="success",
                finished_at=_now(),
                result=result,
                error=None,
            )
        except Exception:
            # Retry with reduced payload so successful runs still surface PR and artifacts.
            fallback_result = _build_result_fallback(result)
            history_store.set_job_fields(
                job_id,
                status="success",
                finished_at=_now(),
                result=fallback_result,
                error=None,
            )
    finally:
        with _JOB_CANCEL_LOCK:
            _JOB_CANCEL_TOKENS.pop(job_id, None)


def enqueue_orchestration(payload: OrchestrateRequest, request_context: Optional[dict] = None) -> dict:
    job_id = str(uuid4())
    created_at = _now()

    request_payload = {
        "jira_ticket_id": payload.jira_ticket_id,
        "repository": payload.repository,
        "base_branch": payload.base_branch,
        "reviewer": payload.reviewer,
        "selected_agent": payload.selected_agent,
        "selected_model": payload.selected_model,
        "commit_message": payload.commit_message,
        "change_plan": payload.change_plan,
        "jira_url": os.environ.get("JIRA_URL"),
        "workspace_dir": _workspace_dir_for_job(job_id),
        **_fetch_jira_details(payload.jira_ticket_id),
    }
    if request_context:
        request_payload.update(request_context)

    get_history_store().create_job(
        job_id=job_id,
        created_at=created_at,
        request_payload=request_payload,
    )

    with _JOB_CANCEL_LOCK:
        _JOB_CANCEL_TOKENS[job_id] = CancellationToken()

    worker = Thread(target=_run_job, args=(job_id, payload), daemon=True)
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@router.post("/orchestrate")
def orchestrate(payload: OrchestrateRequest, _user: dict = Depends(require_run_permission)):
    return enqueue_orchestration(payload)


@router.get("/orchestrate/history")
def orchestrate_history(
    limit: int = Query(default=20, ge=1, le=200),
    include_progress: bool = Query(default=True),
):
    items = get_history_store().list_jobs(limit=limit, include_progress=include_progress)
    return {"items": items}


@router.post("/orchestrate/history/purge")
def purge_orchestrate_history(
    days: int = Query(default=30, ge=1, le=3650),
    _admin: dict = Depends(require_admin),
):
    deleted = get_history_store().purge_old_jobs(days=days)
    return {"deleted": deleted, "days": days}


@router.get("/agents")
def list_agents():
    return {"items": _discover_agent_names()}


@router.get("/orchestrate/{job_id}")
def orchestrate_status(job_id: str):
    job = get_history_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")
    return job


@router.post("/orchestrate/{job_id}/cancel")
def cancel_orchestration(job_id: str, _user: dict = Depends(require_run_permission)):
    history_store = get_history_store()
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    status = str(job.get("status") or "")
    if status in {"success", "failed", "cancelled"}:
        return {"job_id": job_id, "status": status, "cancelled": False}

    with _JOB_CANCEL_LOCK:
        token = _JOB_CANCEL_TOKENS.get(job_id)

    if token is None:
        history_store.set_job_fields(
            job_id,
            status="cancelled",
            finished_at=_now(),
            error="Cancelled by user request",
        )
        return {"job_id": job_id, "status": "cancelled", "cancelled": True}

    token.cancel()
    history_store.append_progress(
        job_id,
        {
            "timestamp": _now(),
            "name": "cancel_requested",
            "status": "running",
            "details": "User requested cancellation",
        },
    )
    return {"job_id": job_id, "status": "cancelling", "cancelled": True}


@router.delete("/orchestrate/{job_id}")
def delete_orchestration(job_id: str, _admin: dict = Depends(require_admin)):
    history_store = get_history_store()
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    status = str(job.get("status") or "")
    if status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Cannot delete a running job. Cancel it first.")

    deleted = history_store.delete_job(job_id)
    return {"job_id": job_id, "deleted": bool(deleted)}
