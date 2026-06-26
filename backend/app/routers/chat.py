from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.jira import service as jira_service
from app.routers.orchestrate import OrchestrateRequest, enqueue_orchestration

router = APIRouter(prefix="/api", tags=["chat"])

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


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, description="Natural language chat input")
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    reviewer: Optional[str] = None
    selected_agent: Optional[str] = None
    selected_model: Optional[str] = None


@router.post("/chat/message")
def chat_message(payload: ChatMessageRequest):
    prompt = payload.message.strip()
    ticket_ids = _extract_ticket_ids(prompt)

    queued_jobs: list[dict] = []
    failed_tickets: list[dict] = []
    for ticket_id in ticket_ids:
        try:
            issue = jira_service.get_issue(ticket_id)
        except Exception as exc:  # noqa: BLE001
            failed_tickets.append({"jira_ticket_id": ticket_id, "error": str(exc)})
            continue

        run_payload = OrchestrateRequest(
            jira_ticket_id=ticket_id,
            repository=payload.repository,
            base_branch=payload.base_branch,
            reviewer=payload.reviewer,
            selected_agent=payload.selected_agent,
            selected_model=payload.selected_model,
            commit_message=f"feat({ticket_id.lower()}): {_first_sentence(prompt)}",
            change_plan=_build_change_plan(prompt, issue),
        )
        queued = enqueue_orchestration(
            run_payload,
            request_context={
                "trigger_source": "chat",
                "chat_prompt": prompt,
                "chat_ticket_ids": ticket_ids,
            },
        )
        queued_jobs.append({"jira_ticket_id": ticket_id, **queued})

    return {
        "assistant_message": _build_assistant_response(prompt, ticket_ids, queued_jobs, failed_tickets),
        "tickets": ticket_ids,
        "queued_jobs": queued_jobs,
        "failed_tickets": failed_tickets,
    }
