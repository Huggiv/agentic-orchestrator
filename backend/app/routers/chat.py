from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
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

_GROOMING_REQUIRED_FIELDS = [
    "problem",
    "user_impact",
    "goals",
    "constraints",
    "acceptance_criteria",
]
_GROOMING_FOLLOW_UP_QUESTIONS = {
    "problem": "What exact problem are we solving?",
    "user_impact": "Who is impacted today, and what is the user/business impact?",
    "goals": "What are the key goals for this change? Please provide 2-5 concise bullets.",
    "constraints": "What constraints should implementation respect (time, security, compatibility, dependencies)?",
    "acceptance_criteria": "What acceptance criteria define done? Please provide measurable bullets.",
}

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


def _normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = re.split(r"\n|;|\|", value)
        cleaned = [part.strip(" -*\t") for part in parts if part.strip(" -*\t")]
        if cleaned:
            return cleaned
    return []


def _normalize_grooming_state(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    pending_field = str(source.get("pending_field") or "").strip()
    if pending_field not in _GROOMING_REQUIRED_FIELDS:
        pending_field = ""
    return {
        "problem": str(source.get("problem") or "").strip(),
        "user_impact": str(source.get("user_impact") or "").strip(),
        "goals": _normalize_text_list(source.get("goals")),
        "constraints": _normalize_text_list(source.get("constraints")),
        "acceptance_criteria": _normalize_text_list(source.get("acceptance_criteria")),
        "pending_field": pending_field or None,
    }


def _extract_prefixed_field(prompt: str, aliases: list[str]) -> str:
    lines = prompt.splitlines()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        for alias in aliases:
            if lower.startswith(alias):
                return line[len(alias) :].strip(" :-")
    return ""


def _extract_prefixed_list(prompt: str, aliases: list[str]) -> list[str]:
    lines = [line.strip() for line in prompt.splitlines() if line.strip()]
    for idx, line in enumerate(lines):
        lower = line.lower()
        for alias in aliases:
            if not lower.startswith(alias):
                continue
            inline = line[len(alias) :].strip(" :-")
            collected = _normalize_text_list(inline)
            for next_line in lines[idx + 1 :]:
                if ":" in next_line and next_line.lower().split(":", 1)[0] in {
                    "problem",
                    "user impact",
                    "impact",
                    "goals",
                    "constraints",
                    "acceptance criteria",
                    "criteria",
                }:
                    break
                bullet = next_line.strip(" -*\t")
                if bullet:
                    collected.append(bullet)
            if collected:
                deduped: list[str] = []
                seen: set[str] = set()
                for item in collected:
                    key = item.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(item)
                return deduped
    return []


def _missing_grooming_fields(state: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field_name in _GROOMING_REQUIRED_FIELDS:
        value = state.get(field_name)
        if isinstance(value, list):
            if len(value) == 0:
                missing.append(field_name)
        elif not str(value or "").strip():
            missing.append(field_name)
    return missing


def _apply_prompt_to_grooming_state(prompt: str, state: dict[str, Any]) -> dict[str, Any]:
    updated = _normalize_grooming_state(state)
    text = prompt.strip()
    if not text:
        return updated

    problem = _extract_prefixed_field(text, ["problem:", "problem statement:"])
    if problem:
        updated["problem"] = problem

    impact = _extract_prefixed_field(text, ["user impact:", "impact:"])
    if impact:
        updated["user_impact"] = impact

    goals = _extract_prefixed_list(text, ["goals:", "goal:"])
    if goals:
        updated["goals"] = goals

    constraints = _extract_prefixed_list(text, ["constraints:", "constraint:"])
    if constraints:
        updated["constraints"] = constraints

    acceptance = _extract_prefixed_list(text, ["acceptance criteria:", "criteria:", "acceptance:"])
    if acceptance:
        updated["acceptance_criteria"] = acceptance

    captured_any = bool(problem or impact or goals or constraints or acceptance)
    if not captured_any and updated.get("pending_field") in _GROOMING_REQUIRED_FIELDS:
        pending_field = str(updated["pending_field"])
        if pending_field in {"goals", "constraints", "acceptance_criteria"}:
            updated[pending_field] = _normalize_text_list(text)
        else:
            updated[pending_field] = text

    return updated


def _recommend_flow_template(state: dict[str, Any]) -> tuple[str, str]:
    corpus_parts = [
        state.get("problem", ""),
        state.get("user_impact", ""),
        " ".join(state.get("goals") or []),
        " ".join(state.get("constraints") or []),
    ]
    corpus = " ".join(str(part) for part in corpus_parts).lower()

    devops_keywords = (
        "deploy",
        "deployment",
        "pipeline",
        "ci",
        "cd",
        "docker",
        "kubernetes",
        "k8s",
        "terraform",
        "infra",
        "infrastructure",
        "ops",
        "sre",
    )
    bugfix_keywords = (
        "bug",
        "fix",
        "regression",
        "error",
        "incident",
        "broken",
        "failure",
    )

    if any(keyword in corpus for keyword in devops_keywords):
        return "devops", "Detected delivery/infrastructure signals in the requirements."
    if any(keyword in corpus for keyword in bugfix_keywords):
        return "bugfix", "Detected stability/regression language indicating a corrective change."
    return "feature", "Defaulted to feature because requirements describe net-new behavior or capability."


def _build_groomed_markdown(state: dict[str, Any]) -> str:
    goals = state.get("goals") or []
    constraints = state.get("constraints") or []
    acceptance = state.get("acceptance_criteria") or []
    lines = [
        "## Grooming Summary",
        f"- Problem: {state.get('problem') or 'TBD'}",
        f"- User impact: {state.get('user_impact') or 'TBD'}",
        "",
        "## Goals",
    ]
    lines.extend(f"- {item}" for item in goals) if goals else lines.append("- TBD")
    lines.append("")
    lines.append("## Constraints")
    lines.extend(f"- {item}" for item in constraints) if constraints else lines.append("- TBD")
    lines.append("")
    lines.append("## Acceptance Criteria")
    lines.extend(f"- {item}" for item in acceptance) if acceptance else lines.append("- TBD")
    return "\n".join(lines)


def _build_jira_prefill(state: dict[str, Any], template: str) -> dict[str, Any]:
    labels = ["agentic-groomed", f"flow-{template}"]
    summary = _first_sentence(state.get("problem") or "Groomed change request", max_len=90)
    description_lines = [
        "h3. Problem",
        str(state.get("problem") or ""),
        "",
        "h3. User Impact",
        str(state.get("user_impact") or ""),
        "",
        "h3. Goals",
    ]
    description_lines.extend(f"- {item}" for item in state.get("goals") or [])
    description_lines.extend(["", "h3. Constraints"])
    description_lines.extend(f"- {item}" for item in state.get("constraints") or [])
    description_lines.extend(["", "h3. Acceptance Criteria"])
    description_lines.extend(f"- {item}" for item in state.get("acceptance_criteria") or [])
    return {
        "summary": summary,
        "description": "\n".join(description_lines).strip(),
        "acceptance_criteria": list(state.get("acceptance_criteria") or []),
        "labels": labels,
    }


def _template_change_plan(template: str, state: dict[str, Any]) -> list[str]:
    goals = list(state.get("goals") or [])
    constraints = list(state.get("constraints") or [])
    acceptance = list(state.get("acceptance_criteria") or [])
    prefix = {
        "feature": "Implement feature scope based on grooming summary",
        "bugfix": "Diagnose root cause and implement targeted bug fix",
        "devops": "Apply infrastructure/delivery improvements with safe rollout",
    }.get(template, "Implement scope based on grooming summary")
    plan = [prefix]
    plan.extend([f"Goal: {item}" for item in goals])
    plan.extend([f"Constraint: {item}" for item in constraints])
    if acceptance:
        plan.append("Validate acceptance criteria before completion")
    return plan


def _build_orchestration_payload_from_grooming(
    state: dict[str, Any],
    *,
    repository: str,
    base_branch: str,
    reviewer: str | None,
    selected_agent: str | None,
    selected_model: str | None,
    jira_ticket_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    template, rationale = _recommend_flow_template(state)
    jira_prefill = _build_jira_prefill(state, template)
    synthetic_ticket_id = jira_ticket_id or f"GROOMING-{uuid4().hex[:6].upper()}"
    payload = {
        "jira_ticket_id": synthetic_ticket_id,
        "repository": repository,
        "base_branch": base_branch,
        "reviewer": reviewer,
        "selected_agent": selected_agent,
        "selected_model": selected_model,
        "commit_message": f"feat({template}): {_first_sentence(state.get('problem') or 'groomed scope')}",
        "change_plan": _template_change_plan(template, state),
        "jira_context": {
            "key": synthetic_ticket_id,
            "summary": jira_prefill["summary"],
            "description": jira_prefill["description"],
            "type": template,
            "labels": jira_prefill["labels"],
        },
    }
    return payload, jira_prefill, template, rationale


def _build_grooming_response(prompt: str, prior_state: dict[str, Any]) -> dict[str, Any]:
    state = _apply_prompt_to_grooming_state(prompt, prior_state)
    missing = _missing_grooming_fields(state)
    is_complete = len(missing) == 0
    follow_up_question = _GROOMING_FOLLOW_UP_QUESTIONS.get(missing[0]) if missing else None
    state["pending_field"] = missing[0] if missing else None

    template, rationale = _recommend_flow_template(state)
    jira_prefill = _build_jira_prefill(state, template)
    groomed_issue = _build_groomed_markdown(state)

    if is_complete:
        assistant_message = (
            f"Grooming summary complete. Recommended flow template: {template}. "
            "Review, assign an agent, and launch orchestration when ready."
        )
    else:
        missing_labels = ", ".join(missing)
        assistant_message = (
            f"Grooming in progress. Missing fields: {missing_labels}. "
            f"{follow_up_question or 'Please provide the missing details.'}"
        )

    return {
        "assistant_message": assistant_message,
        "groomed_issue": groomed_issue,
        "grooming": {
            "schema": {
                "problem": state.get("problem") or "",
                "user_impact": state.get("user_impact") or "",
                "goals": list(state.get("goals") or []),
                "constraints": list(state.get("constraints") or []),
                "acceptance_criteria": list(state.get("acceptance_criteria") or []),
            },
            "missing_fields": missing,
            "follow_up_question": follow_up_question,
            "is_complete": is_complete,
            "recommended_template": template,
            "recommendation_rationale": rationale,
            "jira_prefill": jira_prefill,
            "pending_field": state.get("pending_field"),
        },
    }


class GroomingContext(BaseModel):
    problem: str | None = None
    user_impact: str | None = None
    goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    pending_field: str | None = None


class GroomingAssignRequest(BaseModel):
    grooming: GroomingContext
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    reviewer: Optional[str] = None
    selected_agent: Optional[str] = None
    selected_model: Optional[str] = None
    jira_ticket_id: Optional[str] = None


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, description="Natural language chat input")
    repository: str = Field(min_length=3, description="GitHub repo as owner/repo or clone URL")
    base_branch: str = Field(default="development", min_length=1)
    mode: Literal["support", "grooming"] = "support"
    reviewer: Optional[str] = None
    selected_agent: Optional[str] = None
    selected_model: Optional[str] = None
    grooming_context: GroomingContext | None = None


class ChatConfirmRequest(BaseModel):
    plan_id: str = Field(min_length=8)
    confirm: bool = Field(default=False)


@router.post("/chat/message")
def chat_message(payload: ChatMessageRequest):
    _purge_expired_plans()
    prompt = payload.message.strip()
    if payload.mode == "grooming":
        prior_state = _normalize_grooming_state(payload.grooming_context.model_dump() if payload.grooming_context else {})
        result = _build_grooming_response(prompt, prior_state)
        return {
            "assistant_message": result["assistant_message"],
            "tickets": [],
            "queued_jobs": [],
            "failed_tickets": [],
            "requires_confirmation": False,
            "plan_id": None,
            "groomed_issue": result["groomed_issue"],
            "mode": "grooming",
            "grooming": result["grooming"],
        }

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
            "mode": "support",
            "grooming": None,
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
            "mode": "support",
            "grooming": None,
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
        "mode": "support",
        "grooming": None,
    }


@router.post("/chat/message/stream")
def chat_message_stream(payload: ChatMessageRequest):
    def event_stream():
        _purge_expired_plans()
        prompt = payload.message.strip()
        if payload.mode == "grooming":
            yield _sse_event("status", {"message": "Collecting grooming requirements"})
            prior_state = _normalize_grooming_state(payload.grooming_context.model_dump() if payload.grooming_context else {})
            result = _build_grooming_response(prompt, prior_state)
            for delta in _chunk_text(result["assistant_message"]):
                yield _sse_event("assistant_token", {"delta": delta})
            yield _sse_event(
                "result",
                {
                    "assistant_message": result["assistant_message"],
                    "tickets": [],
                    "queued_jobs": [],
                    "failed_tickets": [],
                    "requires_confirmation": False,
                    "plan_id": None,
                    "groomed_issue": result["groomed_issue"],
                    "mode": "grooming",
                    "grooming": result["grooming"],
                },
            )
            yield _sse_event("done", {})
            return

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
                    "mode": "support",
                    "grooming": None,
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
                    "mode": "support",
                    "grooming": None,
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
            "mode": "support",
            "grooming": None,
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


@router.post("/chat/grooming/assign")
def chat_grooming_assign(payload: GroomingAssignRequest, _user: dict = Depends(require_run_permission)):
    state = _normalize_grooming_state(payload.grooming.model_dump())
    missing = _missing_grooming_fields(state)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Grooming data is incomplete. Missing fields: {', '.join(missing)}",
        )

    run_payload, jira_prefill, template, rationale = _build_orchestration_payload_from_grooming(
        state,
        repository=payload.repository,
        base_branch=payload.base_branch,
        reviewer=payload.reviewer,
        selected_agent=payload.selected_agent,
        selected_model=payload.selected_model,
        jira_ticket_id=payload.jira_ticket_id,
    )

    queued = enqueue_orchestration(
        OrchestrateRequest(**run_payload),
        request_context={
            "trigger_source": "chat_grooming",
            "grooming_summary": _build_groomed_markdown(state),
            "recommended_template": template,
            "recommendation_rationale": rationale,
            "jira_prefill": jira_prefill,
        },
    )

    return {
        "assistant_message": (
            f"Assigned to {template} flow and queued orchestration job {queued['job_id']}."
        ),
        "recommended_template": template,
        "recommendation_rationale": rationale,
        "jira_prefill": jira_prefill,
        "orchestration_payload": run_payload,
        "queued_job": {"jira_ticket_id": run_payload["jira_ticket_id"], **queued},
    }
