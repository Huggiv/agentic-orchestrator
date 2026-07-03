from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, Lock, Thread
from typing import Any, Literal, Optional
from urllib.parse import quote
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator
import requests

from fastapi import APIRouter, Depends, HTTPException, Query

from app.history_store import get_history_store
from app.orchestration import CancellationToken, OrchestrationCancelled, OrchestrationError, run_orchestration
from app.jira import service as jira_service
from app.routers.auth import require_admin, require_run_permission

router = APIRouter(prefix="/api", tags=["orchestrate"])
_JOB_CANCEL_TOKENS: dict[str, CancellationToken] = {}
_JOB_CANCEL_LOCK = Lock()
_JOB_CONTROLS: dict[str, "JobExecutionControl"] = {}
_JOB_CONTROL_LOCK = Lock()

CANONICAL_STEP_STATES = {
    "pending",
    "running",
    "success",
    "failed",
    "skipped",
    "paused",
    "blocked_approval",
}

_STEP_ALIASES = {
    "create_and_checkout_branch": "prepare_branch",
    "read_jira_issue": "read_jira",
    "copilot_agentic_plan": "agentic_implementation",
}

_STEP_ORDER = [
    "clone_repository",
    "checkout_pull_request",
    "read_repo_instructions",
    "auth_setup",
    "prepare_branch",
    "read_jira",
    "select_copilot_agent",
    "agentic_implementation",
    "agentic_pr_review",
    "view_artifacts",
    "commit_changes",
    "push_branch",
    "create_pr",
    "publish_review_comments",
]

_RETRYABLE_STEP_SET = set(_STEP_ORDER)
_JIRA_TICKET_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")


def _normalize_step_name(name: str | None) -> str:
    value = str(name or "").strip()
    if not value:
        return "unknown"
    return _STEP_ALIASES.get(value, value)


def _extract_jira_ticket_from_text(text: str | None) -> str | None:
    value = str(text or "").upper()
    match = _JIRA_TICKET_PATTERN.search(value)
    if not match:
        return None
    return match.group(0)


def _normalize_step_state(status: str | None) -> str:
    value = str(status or "").strip().lower()
    if value in CANONICAL_STEP_STATES:
        return value
    if value in {"queued", "installing", "idle"}:
        return "running"
    if value == "cancelled":
        return "failed"
    return "running"


def _approval_checkpoints_from_env() -> set[str]:
    raw = os.environ.get("AGENT_FLOW_APPROVAL_CHECKPOINTS", "create_pr,publish_review_comments")
    tokens = [item.strip() for item in raw.split(",")]
    return {_normalize_step_name(item) for item in tokens if item}


class JobExecutionControl:
    def __init__(self, approval_checkpoints: set[str]) -> None:
        self._approval_checkpoints = set(approval_checkpoints)
        self._condition = Condition()
        self._paused = False
        self._approval_pending_step: str | None = None
        self._approval_decision: str | None = None

    def pause(self) -> bool:
        with self._condition:
            if self._paused:
                return False
            self._paused = True
            return True

    def resume(self) -> bool:
        with self._condition:
            if not self._paused:
                return False
            self._paused = False
            self._condition.notify_all()
            return True

    def begin_approval_if_needed(self, step_name: str) -> bool:
        step = _normalize_step_name(step_name)
        with self._condition:
            if step not in self._approval_checkpoints:
                return False
            if self._approval_pending_step is not None:
                return False
            self._approval_pending_step = step
            self._approval_decision = None
            self._condition.notify_all()
            return True

    def approve(self) -> bool:
        with self._condition:
            if self._approval_pending_step is None:
                return False
            self._approval_decision = "approved"
            self._condition.notify_all()
            return True

    def reject(self) -> bool:
        with self._condition:
            if self._approval_pending_step is None:
                return False
            self._approval_decision = "rejected"
            self._condition.notify_all()
            return True

    def wait_until_runnable(self, cancel_token: CancellationToken) -> None:
        with self._condition:
            while self._paused:
                if cancel_token.is_cancelled:
                    return
                self._condition.wait(timeout=0.2)

    def wait_for_approval_decision(self, cancel_token: CancellationToken) -> str:
        with self._condition:
            while self._approval_pending_step is not None and self._approval_decision is None:
                if cancel_token.is_cancelled:
                    return "cancelled"
                self._condition.wait(timeout=0.2)

            decision = self._approval_decision or "approved"
            self._approval_pending_step = None
            self._approval_decision = None
            return decision

    def snapshot(self) -> dict[str, str | bool | None]:
        with self._condition:
            return {
                "paused": self._paused,
                "approval_pending_step": self._approval_pending_step,
                "approval_pending": self._approval_pending_step is not None,
            }


class OrchestrateRequest(BaseModel):
    jira_ticket_id: str = Field(min_length=2)
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    reviewer: Optional[str] = None
    selected_agent: Optional[str] = None
    selected_model: Optional[str] = None
    commit_message: str = Field(min_length=3)
    change_plan: list[str] = Field(default_factory=list)
    jira_context: dict[str, Any] | None = None


class RetryRequest(BaseModel):
    retry_mode: Literal["failed_step_only", "from_failed_step"]
    start_step: str | None = None
    override_inputs: dict[str, Any] = Field(default_factory=dict)
    fallback_agent: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_retry_request(self) -> "RetryRequest":
        normalized_start = _normalize_step_name(self.start_step)
        if self.start_step and normalized_start not in _RETRYABLE_STEP_SET:
            raise ValueError(f"Unsupported start_step: {self.start_step}")
        return self


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


def _is_terminal_status(status: str) -> bool:
    return status in {"success", "failed", "cancelled"}


def _build_step_logs(progress: list[dict], result_steps: list[dict] | None = None) -> list[dict]:
    by_step: dict[str, dict] = {}

    for event in progress:
        step_key = _normalize_step_name(event.get("name"))
        item = by_step.setdefault(step_key, {"name": step_key, "state": "pending", "events": []})
        state = _normalize_step_state(event.get("status"))
        item["state"] = state
        item["events"].append(
            {
                "timestamp": event.get("timestamp"),
                "status": state,
                "details": event.get("details"),
            }
        )

    for result_step in result_steps or []:
        step_key = _normalize_step_name(result_step.get("name"))
        item = by_step.setdefault(step_key, {"name": step_key, "state": "pending", "events": []})
        item["state"] = _normalize_step_state(result_step.get("status"))
        if result_step.get("details"):
            item["events"].append(
                {
                    "timestamp": None,
                    "status": item["state"],
                    "details": result_step.get("details"),
                }
            )

    ordered: list[dict] = []
    seen: set[str] = set()
    for step_name in _STEP_ORDER:
        if step_name in by_step:
            ordered.append(by_step[step_name])
            seen.add(step_name)
        else:
            ordered.append({"name": step_name, "state": "pending", "events": []})

    for step_name, entry in by_step.items():
        if step_name not in seen:
            ordered.append(entry)

    return ordered


def _current_and_next_step(step_logs: list[dict], job_status: str) -> tuple[str | None, str | None]:
    if _is_terminal_status(job_status):
        return None, None

    current_step = None
    next_step = None
    for idx, step in enumerate(step_logs):
        state = step.get("state")
        if state in {"running", "paused", "blocked_approval", "failed"}:
            current_step = step.get("name")
            if idx + 1 < len(step_logs):
                next_step = step_logs[idx + 1].get("name")
            break
        if state == "pending" and current_step is None:
            next_step = step.get("name")
            break

    if current_step is None:
        for step in step_logs:
            if step.get("state") == "pending":
                next_step = step.get("name")
                break
    return current_step, next_step


def _allowed_actions(job_status: str, control_snapshot: dict | None) -> list[str]:
    if _is_terminal_status(job_status):
        return []

    if job_status in {"paused", "blocked_approval"} and not control_snapshot:
        # Avoid advertising interactive controls after in-memory controls are cleaned up.
        return []

    snapshot = control_snapshot or {}
    if snapshot.get("approval_pending"):
        return ["approve", "reject", "cancel"]
    if snapshot.get("paused"):
        return ["resume", "cancel"]
    if job_status in {"queued", "running", "paused", "blocked_approval"}:
        return ["pause", "cancel"]
    return ["cancel"]


def _build_status_payload(job: dict) -> dict:
    payload = dict(job)
    progress = list(payload.get("progress") or [])
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    step_logs = _build_step_logs(progress=progress, result_steps=result.get("steps") or [])

    with _JOB_CONTROL_LOCK:
        control = _JOB_CONTROLS.get(str(job.get("id") or ""))
    snapshot = control.snapshot() if control is not None else None

    if snapshot and snapshot.get("approval_pending") and payload.get("status") in {"running", "queued"}:
        payload["status"] = "blocked_approval"
    elif snapshot and snapshot.get("paused") and payload.get("status") in {"running", "queued"}:
        payload["status"] = "paused"

    current_step, next_step = _current_and_next_step(step_logs, str(payload.get("status") or ""))
    payload["step_logs"] = step_logs
    payload["current_step"] = current_step
    payload["next_step"] = next_step
    payload["allowed_actions"] = _allowed_actions(str(payload.get("status") or ""), snapshot)
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    retry_context = request.get("retry_context") if isinstance(request.get("retry_context"), dict) else None
    payload["retry_lineage"] = retry_context
    payload["failure_insight"] = _build_failure_insight(payload, step_logs)
    return payload


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
        "retry_context": result.get("retry_context") if isinstance(result.get("retry_context"), dict) else None,
        "warnings": [
            "Full orchestration result payload could not be persisted; stored fallback fields.",
        ],
    }


def _extract_step_status(steps: list[dict] | None, step_name: str) -> str | None:
    for step in steps or []:
        name = _normalize_step_name(step.get("name"))
        if name == step_name:
            return _normalize_step_state(step.get("status"))
    return None


def _infer_failed_step(job: dict) -> str | None:
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    step_logs = _build_step_logs(progress=list(job.get("progress") or []), result_steps=result.get("steps") or [])
    for step in reversed(step_logs):
        if step.get("state") == "failed":
            return str(step.get("name"))
    return None


def _classify_failure(error: str | None) -> str:
    detail = str(error or "").lower()
    if "authentication" in detail or "token" in detail:
        return "auth_error"
    if "approval rejected" in detail or "rejected at approval checkpoint" in detail:
        return "approval_rejected"
    if "idempotency guard" in detail:
        return "idempotency_guard"
    if "failed" in detail or "error" in detail:
        return "execution_error"
    return "unknown"


def _build_failure_insight(payload: dict, step_logs: list[dict]) -> dict | None:
    if str(payload.get("status") or "") != "failed":
        return None

    failed_step = None
    for step in reversed(step_logs):
        if step.get("state") == "failed":
            failed_step = step.get("name")
            break

    suggestion = "Review logs and adjust retry inputs before rerunning."
    if failed_step in {"commit_changes", "push_branch", "create_pr"}:
        suggestion = "Use retry-from-step and rely on idempotency guard to avoid duplicate side effects."
    elif failed_step == "agentic_implementation":
        suggestion = "Retry with override_inputs.change_plan or fallback_agent for safer regeneration."

    return {
        "failed_step": failed_step,
        "error_class": _classify_failure(payload.get("error")),
        "suggested_action": suggestion,
        "retry_modes": ["failed_step_only", "from_failed_step"] if failed_step else [],
        "can_retry": bool(failed_step),
    }


def _compute_retry_attempt(parent_job: dict) -> int:
    request = parent_job.get("request") if isinstance(parent_job.get("request"), dict) else {}
    retry_ctx = request.get("retry_context") if isinstance(request.get("retry_context"), dict) else {}
    parent_attempt = int(retry_ctx.get("attempt_no") or 0)
    return parent_attempt + 1


def _build_side_effect_guards(parent_job: dict) -> dict[str, Any]:
    result = parent_job.get("result") if isinstance(parent_job.get("result"), dict) else {}
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    return {
        "commit_success": _extract_step_status(steps, "commit_changes") == "success",
        "push_success": _extract_step_status(steps, "push_branch") == "success",
        "pr_success": _extract_step_status(steps, "create_pr") == "success",
        "existing_pr_url": result.get("pull_request_url"),
    }


def _build_retry_request_payload(parent_job: dict, retry_request: RetryRequest, failed_step: str) -> dict:
    request = parent_job.get("request") if isinstance(parent_job.get("request"), dict) else {}
    if not request:
        raise HTTPException(status_code=409, detail="Parent job request payload is unavailable")

    retry_overrides = retry_request.override_inputs if isinstance(retry_request.override_inputs, dict) else {}
    allowed_override_keys = {
        "base_branch",
        "reviewer",
        "selected_model",
        "selected_agent",
        "commit_message",
        "change_plan",
        "jira_context",
    }
    invalid_override_keys = sorted(set(retry_overrides) - allowed_override_keys)
    if invalid_override_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported override_inputs keys: {', '.join(invalid_override_keys)}",
        )

    selected_agent = str(retry_overrides.get("selected_agent") or request.get("selected_agent") or "").strip() or None
    if retry_request.fallback_agent and retry_request.fallback_agent.strip():
        selected_agent = retry_request.fallback_agent.strip()

    payload = {
        "jira_ticket_id": request.get("jira_ticket_id"),
        "repository": request.get("repository"),
        "base_branch": retry_overrides.get("base_branch", request.get("base_branch") or "development"),
        "reviewer": retry_overrides.get("reviewer", request.get("reviewer")),
        "selected_agent": selected_agent,
        "selected_model": retry_overrides.get("selected_model", request.get("selected_model")),
        "commit_message": retry_overrides.get("commit_message", request.get("commit_message")),
        "change_plan": retry_overrides.get("change_plan", request.get("change_plan") or []),
        "jira_context": retry_overrides.get("jira_context", request.get("jira_context")),
    }
    if not payload["jira_ticket_id"] or not payload["repository"] or not payload["commit_message"]:
        raise HTTPException(status_code=409, detail="Parent job payload is incomplete for retry")

    payload["retry_context"] = {
        "parent_job_id": str(parent_job.get("id") or ""),
        "attempt_no": _compute_retry_attempt(parent_job),
        "retry_reason": (retry_request.reason or "").strip() or "Manual retry",
        "retry_mode": retry_request.retry_mode,
        "start_step": failed_step,
        "side_effect_guards": _build_side_effect_guards(parent_job),
    }
    return payload


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


def _normalize_repository_slug(repository: str) -> str:
    value = str(repository or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="Repository is required")

    if value.startswith("https://github.com/"):
        value = value.split("github.com/", 1)[1]
    if value.endswith(".git"):
        value = value[:-4]
    value = value.strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        raise HTTPException(status_code=422, detail="Repository must be in owner/repo format or GitHub URL")
    return value


def _github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "agent-flow-orchestrator",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("COPILOT_GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _run_job(job_id: str, payload: OrchestrateRequest, retry_context: dict | None = None) -> None:
    with _JOB_CANCEL_LOCK:
        cancel_token = _JOB_CANCEL_TOKENS.get(job_id)
    with _JOB_CONTROL_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if cancel_token is None:
        return

    history_store = get_history_store()
    history_store.set_job_fields(job_id, status="running", started_at=_now())

    def progress_callback(event: dict) -> None:
        normalized_event = {
            "timestamp": str(event.get("timestamp") or _now()),
            "name": _normalize_step_name(event.get("name")),
            "status": _normalize_step_state(str(event.get("status") or "running")),
            "details": event.get("details"),
        }
        if isinstance(event.get("artifacts"), list):
            normalized_event["artifacts"] = event.get("artifacts")

        if control is not None:
            control.wait_until_runnable(cancel_token)
            cancel_token.throw_if_cancelled()

        history_store.append_progress(job_id, normalized_event)

        is_checkpoint = (
            control is not None
            and normalized_event["status"] == "running"
            and control.begin_approval_if_needed(normalized_event["name"])
        )
        if is_checkpoint:
            history_store.set_job_fields(job_id, status="blocked_approval")
            history_store.append_progress(
                job_id,
                {
                    "timestamp": _now(),
                    "name": normalized_event["name"],
                    "status": "blocked_approval",
                    "details": "Awaiting approval",
                },
            )

            decision = control.wait_for_approval_decision(cancel_token)
            if decision == "rejected":
                history_store.append_progress(
                    job_id,
                    {
                        "timestamp": _now(),
                        "name": normalized_event["name"],
                        "status": "failed",
                        "details": "Approval rejected",
                    },
                )
                raise OrchestrationError(
                    f"Execution rejected at approval checkpoint: {normalized_event['name']}"
                )

            if not cancel_token.is_cancelled:
                history_store.set_job_fields(job_id, status="running")
                history_store.append_progress(
                    job_id,
                    {
                        "timestamp": _now(),
                        "name": normalized_event["name"],
                        "status": "running",
                        "details": "Approval granted; continuing",
                    },
                )

    try:
        run_kwargs = {
            "jira_ticket_id": payload.jira_ticket_id,
            "repository": payload.repository,
            "base_branch": payload.base_branch,
            "reviewer": payload.reviewer,
            "selected_agent": payload.selected_agent,
            "selected_model": payload.selected_model,
            "commit_message": payload.commit_message,
            "change_plan": payload.change_plan,
            "jira_context": payload.jira_context,
            "progress_callback": progress_callback,
            "cancellation_token": cancel_token,
            "run_id": f"agent_flow-agentic-{job_id[:8]}",
        }
        if retry_context:
            run_kwargs["retry_context"] = retry_context
        result = run_orchestration(**run_kwargs)
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
        with _JOB_CONTROL_LOCK:
            _JOB_CONTROLS.pop(job_id, None)


def enqueue_orchestration(
    payload: OrchestrateRequest,
    request_context: Optional[dict] = None,
    retry_context: Optional[dict] = None,
) -> dict:
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
        "jira_context": payload.jira_context,
        "jira_url": os.environ.get("JIRA_URL"),
        "workspace_dir": _workspace_dir_for_job(job_id),
    }
    if payload.jira_context:
        request_payload.update(
            {
                "jira_title": payload.jira_context.get("summary", ""),
                "jira_summary": payload.jira_context.get("summary", ""),
                "jira_description": payload.jira_context.get("description", ""),
                "jira_type": payload.jira_context.get("type", ""),
            }
        )
    else:
        request_payload.update(_fetch_jira_details(payload.jira_ticket_id))
    if request_context:
        request_payload.update(request_context)
    if retry_context:
        request_payload["retry_context"] = retry_context

    get_history_store().create_job(
        job_id=job_id,
        created_at=created_at,
        request_payload=request_payload,
    )

    with _JOB_CANCEL_LOCK:
        _JOB_CANCEL_TOKENS[job_id] = CancellationToken()
    with _JOB_CONTROL_LOCK:
        _JOB_CONTROLS[job_id] = JobExecutionControl(_approval_checkpoints_from_env())

    worker = Thread(target=_run_job, args=(job_id, payload, retry_context), daemon=True)
    worker.start()
    return {"job_id": job_id, "status": "queued"}


@router.post("/orchestrate")
def orchestrate(payload: OrchestrateRequest, _user: dict = Depends(require_run_permission)):
    return enqueue_orchestration(payload)


@router.get("/orchestrate/pr-review/pulls")
def list_repo_pull_requests(repository: str = Query(min_length=3), _user: dict = Depends(require_run_permission)):
    slug = _normalize_repository_slug(repository)
    url = f"https://api.github.com/repos/{quote(slug, safe='/')}/pulls"
    params = {"state": "open", "per_page": 100, "sort": "updated", "direction": "desc"}

    try:
        response = requests.get(url, headers=_github_api_headers(), params=params, timeout=20)
    except requests.RequestException as exc:  # pragma: no cover - network errors depend on runtime
        raise HTTPException(status_code=502, detail=f"Unable to reach GitHub API: {exc}") from exc

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository not found or inaccessible")
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=response.status_code, detail="GitHub API authorization failed")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"GitHub API error ({response.status_code})")

    payload = response.json()
    if not isinstance(payload, list):
        raise HTTPException(status_code=502, detail="Unexpected response from GitHub API")

    pulls: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        title = str(item.get("title") or "").strip() or f"PR #{number}"
        pulls.append(
            {
                "number": number,
                "title": title,
                "label": f"#{number} - {title}",
                "jira_ticket_id": _extract_jira_ticket_from_text(title),
                "url": str(item.get("html_url") or ""),
                "head_ref": str((item.get("head") or {}).get("ref") or ""),
                "base_ref": str((item.get("base") or {}).get("ref") or ""),
                "author": str((item.get("user") or {}).get("login") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )

    pulls.sort(key=lambda pr: pr["number"], reverse=True)
    return {"repository": slug, "pulls": pulls}


@router.post("/orchestrate/{job_id}/retry")
def retry_orchestration(job_id: str, payload: RetryRequest, _user: dict = Depends(require_run_permission)):
    history_store = get_history_store()
    parent_job = history_store.get_job(job_id)
    if not parent_job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    if str(parent_job.get("status") or "") != "failed":
        raise HTTPException(status_code=409, detail="Retries are only allowed for failed jobs")

    failed_step = _normalize_step_name(payload.start_step)
    if not payload.start_step:
        inferred = _infer_failed_step(parent_job)
        if not inferred:
            raise HTTPException(status_code=409, detail="Could not infer failed step from parent job")
        failed_step = inferred
    if failed_step not in _RETRYABLE_STEP_SET:
        raise HTTPException(status_code=422, detail=f"Unsupported retry start step: {failed_step}")

    retry_source = _build_retry_request_payload(parent_job, payload, failed_step)
    retry_context = retry_source.pop("retry_context")
    retry_payload = OrchestrateRequest(**retry_source)
    queued = enqueue_orchestration(
        retry_payload,
        request_context={"trigger_source": "retry"},
        retry_context=retry_context,
    )
    queued["parent_job_id"] = job_id
    queued["retry_context"] = retry_context
    return queued


@router.get("/orchestrate/history")
def orchestrate_history(
    limit: int = Query(default=20, ge=1, le=200),
    include_progress: bool = Query(default=True),
):
    items = get_history_store().list_jobs(limit=limit, include_progress=include_progress)
    return {"items": [_build_status_payload(item) for item in items]}


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
    return _build_status_payload(job)


@router.post("/orchestrate/{job_id}/pause")
def pause_orchestration(job_id: str, _user: dict = Depends(require_run_permission)):
    history_store = get_history_store()
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    status = str(job.get("status") or "")
    if _is_terminal_status(status):
        raise HTTPException(status_code=409, detail="Cannot pause a completed job")

    with _JOB_CONTROL_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if control is None:
        raise HTTPException(status_code=409, detail="Job is not active")
    snapshot = control.snapshot()
    if snapshot.get("approval_pending"):
        raise HTTPException(status_code=409, detail="Cannot pause while awaiting approval")
    if snapshot.get("paused"):
        return {"job_id": job_id, "status": "paused", "paused": False}

    control.pause()
    history_store.set_job_fields(job_id, status="paused")
    history_store.append_progress(
        job_id,
        {
            "timestamp": _now(),
            "name": "workflow_control",
            "status": "paused",
            "details": "User requested pause",
        },
    )
    return {"job_id": job_id, "status": "paused", "paused": True}


@router.post("/orchestrate/{job_id}/resume")
def resume_orchestration(job_id: str, _user: dict = Depends(require_run_permission)):
    history_store = get_history_store()
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    with _JOB_CONTROL_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if control is None:
        raise HTTPException(status_code=409, detail="Job is not active")

    if not control.resume():
        raise HTTPException(status_code=409, detail="Job is not paused")

    history_store.set_job_fields(job_id, status="running")
    history_store.append_progress(
        job_id,
        {
            "timestamp": _now(),
            "name": "workflow_control",
            "status": "running",
            "details": "User resumed execution",
        },
    )
    return {"job_id": job_id, "status": "running", "resumed": True}


def _handle_approval_action(job_id: str, action: str) -> dict:
    history_store = get_history_store()
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    with _JOB_CONTROL_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if control is None:
        raise HTTPException(status_code=409, detail="Job is not active")

    snapshot = control.snapshot()
    if not snapshot.get("approval_pending"):
        raise HTTPException(status_code=409, detail="No approval checkpoint is currently blocked")

    step_name = str(snapshot.get("approval_pending_step") or "checkpoint")
    if action == "approve":
        if not control.approve():
            raise HTTPException(status_code=409, detail="No approval checkpoint is currently blocked")
        history_store.set_job_fields(job_id, status="running")
        history_store.append_progress(
            job_id,
            {
                "timestamp": _now(),
                "name": step_name,
                "status": "running",
                "details": "Approval action: approved",
            },
        )
        return {"job_id": job_id, "status": "running", "decision": "approved"}

    if not control.reject():
        raise HTTPException(status_code=409, detail="No approval checkpoint is currently blocked")
    history_store.append_progress(
        job_id,
        {
            "timestamp": _now(),
            "name": step_name,
            "status": "failed",
            "details": "Approval action: rejected",
        },
    )
    return {"job_id": job_id, "status": "blocked_approval", "decision": "rejected"}


@router.post("/orchestrate/{job_id}/approve")
def approve_orchestration_checkpoint(job_id: str, _user: dict = Depends(require_run_permission)):
    return _handle_approval_action(job_id, action="approve")


@router.post("/orchestrate/{job_id}/reject")
def reject_orchestration_checkpoint(job_id: str, _user: dict = Depends(require_run_permission)):
    return _handle_approval_action(job_id, action="reject")


@router.post("/orchestrate/{job_id}/cancel")
def cancel_orchestration(job_id: str, _user: dict = Depends(require_run_permission)):
    history_store = get_history_store()
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    status = str(job.get("status") or "")
    if _is_terminal_status(status):
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
    with _JOB_CONTROL_LOCK:
        control = _JOB_CONTROLS.get(job_id)
    if control is not None:
        control.resume()
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
    if status in {"queued", "running", "paused", "blocked_approval"}:
        raise HTTPException(status_code=409, detail="Cannot delete a running job. Cancel it first.")

    deleted = history_store.delete_job(job_id)
    return {"job_id": job_id, "deleted": bool(deleted)}
