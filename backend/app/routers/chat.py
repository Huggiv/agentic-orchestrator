from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.orchestration import OrchestrationError, _prepare_env, _run_copilot_prompt
from app.jira import service as jira_service
from app.history_store import get_history_store
from app.routers.auth import require_run_permission
from app.routers.orchestrate import OrchestrateRequest, cancel_orchestration, enqueue_orchestration

router = APIRouter(prefix="/api", tags=["chat"])
_CHAT_PLAN_TTL = timedelta(minutes=20)
_PENDING_CHAT_PLANS: dict[str, dict] = {}

JIRA_TICKET_PATTERN = re.compile(r"\b[A-Z][A-Z0-9_]+-\d+\b")


def _extract_ticket_ids(text: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for match in JIRA_TICKET_PATTERN.findall(text.upper()):
        if match in seen:
            continue
        seen.add(match)
        ordered.append(match)
    return ordered


def _first_sentence(text: str, max_len: int = 72) -> str:
    compact = " ".join((text or "").split())
    if not compact:
        return "automated implementation"
    sentence = compact.split(".", 1)[0].strip()
    if not sentence:
        sentence = compact
    if len(sentence) <= max_len:
        return sentence
    return sentence[: max_len - 3].rstrip() + "..."


def _build_change_plan(prompt: str, issue: dict) -> list[str]:
    summary = str(issue.get("summary") or "").strip()
    issue_type = str(issue.get("type") or "").strip()
    plan = [
        "Analyze impacted files and dependencies",
        "Implement requested behavior with small safe changes",
        "Run tests and lint checks before finalizing",
    ]
    if summary:
        plan.append(f"Primary Jira objective: {summary}")
    if issue_type:
        plan.append(f"Issue type: {issue_type}")

    guidance = " ".join(prompt.split())
    if guidance:
        plan.append(f"Prompt grooming guidance: {guidance}")
    return plan


def _build_assistant_response(
    prompt: str,
    ticket_ids: list[str],
    queued_jobs: list[dict],
    failed_tickets: list[dict],
) -> str:
    if not ticket_ids:
        return (
            "I can run agentic workflows from chat, including multiple tickets in one message. "
            "Please include at least one Jira ticket key such as AGENT_FLOW-101, plus any grooming instructions."
        )

    ticket_csv = ", ".join(ticket_ids)
    if not queued_jobs:
        return (
            f"I found tickets in your prompt ({ticket_csv}), but none could be queued. "
            "Please verify Jira access and ticket validity, then retry."
        )

    job_summaries = ", ".join(f"{item['jira_ticket_id']} ({item['job_id']})" for item in queued_jobs)
    message = (
        f"Queued {len(queued_jobs)} workflow run(s) from your prompt for: {ticket_csv}. "
        f"Each run is groomed with your prompt instructions and tracked independently in history/executing views. "
        f"Queued jobs: {job_summaries}."
    )
    if failed_tickets:
        failed_csv = ", ".join(item["jira_ticket_id"] for item in failed_tickets)
        message += f" Skipped ticket(s): {failed_csv}."
    return message


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


def _chunk_text(text: str, chunk_size: int = 22) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    for idx in range(0, len(words), chunk_size):
        piece = " ".join(words[idx : idx + chunk_size])
        if idx + chunk_size < len(words):
            piece = f"{piece} "
        chunks.append(piece)
    return chunks


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _purge_expired_plans() -> None:
    now = _now()
    expired = [plan_id for plan_id, item in _PENDING_CHAT_PLANS.items() if item["expires_at"] <= now]
    for plan_id in expired:
        _PENDING_CHAT_PLANS.pop(plan_id, None)


def _build_grooming_prompt(prompt: str, ticket_ids: list[str], issues: list[dict]) -> str:
    issue_blocks = []
    for issue in issues:
        issue_blocks.append(
            "\n".join(
                [
                    f"Ticket: {issue.get('key', '')}",
                    f"Summary: {issue.get('summary', '')}",
                    f"Type: {issue.get('type', '')}",
                    f"Description: {issue.get('description', '')}",
                ]
            )
        )
    issue_text = "\n\n".join(issue_blocks)
    ticket_csv = ", ".join(ticket_ids)
    return (
        "You are a Jira grooming assistant.\n"
        f"User prompt: {prompt}\n"
        f"Tickets: {ticket_csv}\n\n"
        f"Issue details:\n{issue_text}\n\n"
        "Return a concise markdown response with sections:\n"
        "1) Groomed Scope\n"
        "2) Acceptance Criteria\n"
        "3) Risks\n"
        "4) Suggested Implementation Plan (short bullets)\n"
        "Keep it actionable and implementation-ready."
    )


def _groom_with_llm(prompt: str, ticket_ids: list[str], issues: list[dict], selected_model: str | None) -> str:
    env = _prepare_env()
    grooming_prompt = _build_grooming_prompt(prompt, ticket_ids, issues)
    agent_name = "SWE"
    try:
        output = _run_copilot_prompt(
            grooming_prompt,
            cwd="/tmp",
            env=env,
            agent_name=agent_name,
            model=selected_model,
        )
        return (output or "").strip()
    except OrchestrationError:
        return (
            "## Groomed Scope\n"
            f"- Tickets: {', '.join(ticket_ids)}\n"
            "- Prepared from Jira details and prompt context.\n\n"
            "## Acceptance Criteria\n"
            "- Behavior matches Jira intent\n"
            "- Tests updated\n\n"
            "## Risks\n"
            "- Environment/auth may affect automation quality\n\n"
            "## Suggested Implementation Plan\n"
            "- Analyze impacted files\n"
            "- Apply focused changes\n"
            "- Validate with tests"
        )


def _respond_with_llm(prompt: str, selected_model: str | None) -> str:
    env = _prepare_env()
    chat_prompt = (
        "You are a concise engineering assistant in a web chat. "
        "Answer the user request in 3-6 short bullet points, practical and actionable.\n\n"
        f"User request:\n{prompt}"
    )
    try:
        output = _run_copilot_prompt(
            chat_prompt,
            cwd="/tmp",
            env=env,
            agent_name="SWE",
            model=selected_model,
        )
        text = (output or "").strip()
        return text or "I can help with that. Share more context and I will provide a concise plan."
    except OrchestrationError:
        return "I can help with that. Share Jira tickets to run workflows, or provide more context for a concise solution."


def _build_plan_change_plan(prompt: str, issue: dict, groomed_plan: str) -> list[str]:
    summary = str(issue.get("summary") or "").strip()
    issue_type = str(issue.get("type") or "").strip()
    plan = [
        "Analyze impacted files and dependencies",
        "Implement requested behavior with small safe changes",
        "Run tests and lint checks before finalizing",
    ]
    if summary:
        plan.append(f"Primary Jira objective: {summary}")
    if issue_type:
        plan.append(f"Issue type: {issue_type}")
    guidance = " ".join(prompt.split())
    if guidance:
        plan.append(f"Prompt grooming guidance: {guidance}")
    if groomed_plan:
        plan.append(f"Groomed plan summary: {' '.join(groomed_plan.split())[:400]}")
    return plan


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, description="Natural language chat input")
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    reviewer: Optional[str] = None
    selected_agent: Optional[str] = None
    selected_model: Optional[str] = None


class ChatConfirmRequest(BaseModel):
    plan_id: str = Field(min_length=8)
    confirm: bool = Field(default=False)


@router.post("/chat/message")
def chat_message(payload: ChatMessageRequest):
    _purge_expired_plans()
    prompt = payload.message.strip()
    ticket_ids = _extract_ticket_ids(prompt)
    if not ticket_ids:
        llm_reply = _respond_with_llm(prompt, payload.selected_model)
        return {
            "assistant_message": llm_reply,
            "tickets": [],
            "queued_jobs": [],
            "failed_tickets": [],
            "requires_confirmation": False,
            "plan_id": None,
            "groomed_issue": None,
        }

    failed_tickets: list[dict] = []
    issues: list[dict] = []
    for ticket_id in ticket_ids:
        try:
            issues.append(jira_service.get_issue(ticket_id))
        except Exception as exc:  # noqa: BLE001
            failed_tickets.append({"jira_ticket_id": ticket_id, "error": str(exc)})

    valid_ticket_ids = [str(issue.get("key") or "").upper() for issue in issues if issue.get("key")]
    if not valid_ticket_ids:
        return {
            "assistant_message": "Unable to load any referenced Jira tickets. Please verify ticket IDs and Jira connectivity.",
            "tickets": ticket_ids,
            "queued_jobs": [],
            "failed_tickets": failed_tickets,
            "requires_confirmation": False,
            "plan_id": None,
            "groomed_issue": None,
        }

    groomed_issue = _groom_with_llm(prompt, valid_ticket_ids, issues, payload.selected_model)
    plan_id = f"plan-{uuid4().hex}"
    _PENDING_CHAT_PLANS[plan_id] = {
        "created_at": _now(),
        "expires_at": _now() + _CHAT_PLAN_TTL,
        "prompt": prompt,
        "repository": payload.repository,
        "base_branch": payload.base_branch,
        "reviewer": payload.reviewer,
        "selected_agent": payload.selected_agent,
        "selected_model": payload.selected_model,
        "ticket_ids": valid_ticket_ids,
        "issues": issues,
        "groomed_issue": groomed_issue,
    }

    failed_csv = ", ".join(item["jira_ticket_id"] for item in failed_tickets) if failed_tickets else None
    assistant_message = (
        f"I groomed {len(valid_ticket_ids)} ticket(s): {', '.join(valid_ticket_ids)}. "
        "Review the groomed issue and confirm before I trigger workflows."
    )
    if failed_csv:
        assistant_message += f" Skipped: {failed_csv}."

    return {
        "assistant_message": assistant_message,
        "tickets": valid_ticket_ids,
        "queued_jobs": [],
        "failed_tickets": failed_tickets,
        "requires_confirmation": True,
        "plan_id": plan_id,
        "groomed_issue": groomed_issue,
    }


@router.post("/chat/message/stream")
def chat_message_stream(payload: ChatMessageRequest):
    def event_stream():
        _purge_expired_plans()
        prompt = payload.message.strip()
        yield _sse_event("status", {"message": "Analyzing prompt"})

        ticket_ids = _extract_ticket_ids(prompt)
        yield _sse_event("tickets", {"tickets": ticket_ids})

        if not ticket_ids:
            yield _sse_event("status", {"message": "Generating concise response"})
            assistant_message = _respond_with_llm(prompt, payload.selected_model)
            for delta in _chunk_text(assistant_message):
                yield _sse_event("assistant_token", {"delta": delta})
            yield _sse_event(
                "result",
                {
                    "assistant_message": assistant_message,
                    "tickets": [],
                    "queued_jobs": [],
                    "failed_tickets": [],
                    "requires_confirmation": False,
                    "plan_id": None,
                    "groomed_issue": None,
                },
            )
            yield _sse_event("done", {})
            return

        failed_tickets: list[dict] = []
        issues: list[dict] = []
        for ticket_id in ticket_ids:
            yield _sse_event("status", {"message": f"Loading Jira ticket {ticket_id}"})
            try:
                issue = jira_service.get_issue(ticket_id)
            except Exception as exc:  # noqa: BLE001
                failed = {"jira_ticket_id": ticket_id, "error": str(exc)}
                failed_tickets.append(failed)
                yield _sse_event("ticket_failed", failed)
                continue
            issues.append(issue)

        valid_ticket_ids = [str(issue.get("key") or "").upper() for issue in issues if issue.get("key")]
        if not valid_ticket_ids:
            assistant_message = "Unable to load any referenced Jira tickets. Please verify ticket IDs and Jira connectivity."
            for delta in _chunk_text(assistant_message):
                yield _sse_event("assistant_token", {"delta": delta})
            yield _sse_event(
                "result",
                {
                    "assistant_message": assistant_message,
                    "tickets": ticket_ids,
                    "queued_jobs": [],
                    "failed_tickets": failed_tickets,
                    "requires_confirmation": False,
                    "plan_id": None,
                    "groomed_issue": None,
                },
            )
            yield _sse_event("done", {})
            return

        yield _sse_event("status", {"message": "Grooming issue details with LLM"})
        groomed_issue = _groom_with_llm(prompt, valid_ticket_ids, issues, payload.selected_model)
        plan_id = f"plan-{uuid4().hex}"
        _PENDING_CHAT_PLANS[plan_id] = {
            "created_at": _now(),
            "expires_at": _now() + _CHAT_PLAN_TTL,
            "prompt": prompt,
            "repository": payload.repository,
            "base_branch": payload.base_branch,
            "reviewer": payload.reviewer,
            "selected_agent": payload.selected_agent,
            "selected_model": payload.selected_model,
            "ticket_ids": valid_ticket_ids,
            "issues": issues,
            "groomed_issue": groomed_issue,
        }

        failed_csv = ", ".join(item["jira_ticket_id"] for item in failed_tickets) if failed_tickets else None
        assistant_message = (
            f"I groomed {len(valid_ticket_ids)} ticket(s): {', '.join(valid_ticket_ids)}. "
            "Review the groomed issue and confirm before I trigger workflows."
        )
        if failed_csv:
            assistant_message += f" Skipped: {failed_csv}."

        for delta in _chunk_text(assistant_message):
            yield _sse_event("assistant_token", {"delta": delta})

        result_payload = {
            "assistant_message": assistant_message,
            "tickets": valid_ticket_ids,
            "queued_jobs": [],
            "failed_tickets": failed_tickets,
            "requires_confirmation": True,
            "plan_id": plan_id,
            "groomed_issue": groomed_issue,
        }
        yield _sse_event("result", result_payload)
        yield _sse_event("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/cancel/{job_id}")
def chat_cancel_job(job_id: str, _user: dict = Depends(require_run_permission)):
    job = get_history_store().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Orchestration job not found")

    request_payload = job.get("request") or {}
    if request_payload.get("trigger_source") != "chat":
        raise HTTPException(status_code=409, detail="Only chat-triggered jobs can be cancelled from chat")

    return cancel_orchestration(job_id)


@router.post("/chat/confirm")
def chat_confirm(payload: ChatConfirmRequest, _user: dict = Depends(require_run_permission)):
    _purge_expired_plans()
    plan = _PENDING_CHAT_PLANS.get(payload.plan_id)
    if not plan:
        return {
            "assistant_message": "This grooming plan expired or does not exist. Please request grooming again.",
            "queued_jobs": [],
            "confirmed": False,
        }

    if not payload.confirm:
        _PENDING_CHAT_PLANS.pop(payload.plan_id, None)
        return {
            "assistant_message": "Understood. I did not trigger any workflow.",
            "queued_jobs": [],
            "confirmed": False,
        }

    queued_jobs: list[dict] = []
    issues_by_key = {
        str(issue.get("key") or "").upper(): issue
        for issue in plan.get("issues", [])
        if issue.get("key")
    }
    for ticket_id in plan.get("ticket_ids", []):
        issue = issues_by_key.get(ticket_id)
        if not issue:
            continue
        run_payload = OrchestrateRequest(
            jira_ticket_id=ticket_id,
            repository=plan["repository"],
            base_branch=plan["base_branch"],
            reviewer=plan.get("reviewer"),
            selected_agent=plan.get("selected_agent"),
            selected_model=plan.get("selected_model"),
            commit_message=f"feat({ticket_id.lower()}): {_first_sentence(plan['prompt'])}",
            change_plan=_build_plan_change_plan(plan["prompt"], issue, plan.get("groomed_issue", "")),
        )
        queued = enqueue_orchestration(
            run_payload,
            request_context={
                "trigger_source": "chat",
                "chat_prompt": plan["prompt"],
                "chat_ticket_ids": plan["ticket_ids"],
                "chat_plan_id": payload.plan_id,
            },
        )
        queued_jobs.append({"jira_ticket_id": ticket_id, **queued})

    _PENDING_CHAT_PLANS.pop(payload.plan_id, None)
    return {
        "assistant_message": (
            f"Confirmed. Triggered {len(queued_jobs)} workflow run(s): "
            + ", ".join(f"{item['jira_ticket_id']} ({item['job_id']})" for item in queued_jobs)
        ),
        "queued_jobs": queued_jobs,
        "confirmed": True,
    }
