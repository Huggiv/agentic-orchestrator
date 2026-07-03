from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel, Field

from app.orchestration import OrchestrationError, _prepare_env, _run_copilot_prompt
from app.jira import service as jira_service
from app.history_store import get_history_store
from app.routers.auth import require_run_permission
from app.routers.orchestrate import OrchestrateRequest, enqueue_orchestration

router = APIRouter(prefix="/api", tags=["chat"])

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


class ChatSessionCreateRequest(BaseModel):
    title: str | None = None
    mode: Literal["interactive", "support", "grooming"] = "interactive"
    model: str | None = None
    client_context: dict[str, Any] = Field(default_factory=dict)


class ChatSessionMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    model: str | None = None
    mode: Literal["interactive", "support", "grooming"] = "interactive"
    client_context: dict[str, Any] = Field(default_factory=dict)


class ChatPrepareTriggerRequest(BaseModel):
    selected_agent: str | None = None
    repository: str | None = None
    base_branch: str | None = None
    reviewer: str | None = None
    selected_model: str | None = None


class ChatConfirmTriggerRequest(BaseModel):
    confirm: bool = True
    idempotency_key: str | None = None


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_title(title: str | None, fallback: str) -> str:
    value = str(title or "").strip()
    if not value:
        value = fallback
    return value[:80]


def _chat_session_or_404(session_id: str) -> dict[str, Any]:
    session = get_history_store().get_chat_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "Chat session not found"})
    return session


def _ensure_open_session(session: dict[str, Any]) -> None:
    if str(session.get("status") or "") != "open":
        raise HTTPException(status_code=409, detail={"code": "SESSION_CLOSED", "message": "Chat session is closed"})


def _session_metadata(session: dict[str, Any]) -> dict[str, Any]:
    meta = session.get("metadata")
    if isinstance(meta, dict):
        return dict(meta)
    return {}


def _effective_mode(session_mode: str, request_mode: str) -> str:
    candidate = request_mode if request_mode != "interactive" else session_mode
    if candidate not in {"support", "grooming"}:
        return "support"
    return candidate


def _append_chat_message(
    *,
    session_id: str,
    role: str,
    kind: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    created_at = _utc_iso_now()
    message_id = f"msg-{uuid4().hex}"
    get_history_store().append_chat_message(
        message_id=message_id,
        session_id=session_id,
        role=role,
        kind=kind,
        content=content,
        created_at=created_at,
        payload=payload,
    )
    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "kind": kind,
        "content": content,
        "created_at": created_at,
        "payload": payload,
    }


def _derive_jira_seed_state(prompt: str, issues: list[dict], existing_state: dict[str, Any]) -> dict[str, Any]:
    seeded = _normalize_grooming_state(existing_state)
    if not seeded.get("problem"):
        seeded["problem"] = str((issues[0].get("summary") if issues else "") or prompt).strip()
    if not seeded.get("user_impact"):
        seeded["user_impact"] = "Impact inferred from Jira context; refine with user-facing impact details."
    if not seeded.get("goals"):
        seeded["goals"] = [
            f"Address Jira scope for {str(issue.get('key') or '').upper()}" for issue in issues if issue.get("key")
        ]
    return seeded


@router.post("/chat/sessions")
def create_chat_session(payload: ChatSessionCreateRequest, _user: dict = Depends(require_run_permission)):
    now = _utc_iso_now()
    session_id = f"chat-{uuid4().hex}"
    mode = "support" if payload.mode == "interactive" else payload.mode
    metadata = {
        "client_context": payload.client_context,
        "grooming_state": _normalize_grooming_state({}),
        "trigger_state": "draft",
        "prepared_payload": None,
        "queued_job": None,
        "last_ticket_ids": [],
        "model": payload.model,
    }
    title = _trim_title(payload.title, "New Chat")
    get_history_store().create_chat_session(
        session_id=session_id,
        title=title,
        mode=mode,
        selected_model=payload.model,
        created_at=now,
        metadata=metadata,
    )
    return {
        "session_id": session_id,
        "title": title,
        "status": "open",
        "mode": mode,
        "selected_model": payload.model,
        "created_at": now,
        "updated_at": now,
    }


@router.get("/chat/sessions")
def list_chat_sessions(limit: int = Query(default=30, ge=1, le=100), _user: dict = Depends(require_run_permission)):
    sessions = get_history_store().list_chat_sessions(limit=limit)
    return {"sessions": sessions}


@router.get("/chat/sessions/{session_id}")
def get_chat_session(session_id: str, _user: dict = Depends(require_run_permission)):
    session = _chat_session_or_404(session_id)
    messages = get_history_store().list_chat_messages(session_id)
    return {
        "session": session,
        "message_count": len(messages),
        "last_message": messages[-1] if messages else None,
    }


@router.get("/chat/sessions/{session_id}/messages")
def get_chat_session_messages(session_id: str, _user: dict = Depends(require_run_permission)):
    _chat_session_or_404(session_id)
    messages = get_history_store().list_chat_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.post("/chat/sessions/{session_id}/messages")
def send_chat_session_message(
    session_id: str,
    payload: ChatSessionMessageRequest,
    _user: dict = Depends(require_run_permission),
):
    session = _chat_session_or_404(session_id)
    _ensure_open_session(session)
    metadata = _session_metadata(session)

    prompt = payload.message.strip()
    user_message = _append_chat_message(
        session_id=session_id,
        role="user",
        kind="text",
        content=prompt,
        payload={"mode": payload.mode, "client_context": payload.client_context},
    )

    session_mode = _effective_mode(str(session.get("mode") or "support"), payload.mode)
    selected_model = payload.model or metadata.get("model") or session.get("selected_model")

    if session_mode == "grooming":
        prior_state = _normalize_grooming_state(metadata.get("grooming_state") or {})
        result = _build_grooming_response(prompt, prior_state)
        grooming_payload = result.get("grooming") or {}
        schema = grooming_payload.get("schema") or {}
        state = _normalize_grooming_state({**schema, "pending_field": grooming_payload.get("pending_field")})
        metadata["grooming_state"] = state
        metadata["trigger_state"] = "ready_to_trigger" if grooming_payload.get("is_complete") else "grooming"
        metadata["model"] = selected_model
        metadata["client_context"] = payload.client_context or metadata.get("client_context") or {}

        get_history_store().update_chat_session(
            session_id,
            mode="grooming",
            selected_model=selected_model,
            updated_at=_utc_iso_now(),
            metadata=metadata,
        )

        assistant_message = _append_chat_message(
            session_id=session_id,
            role="assistant",
            kind="text",
            content=str(result.get("assistant_message") or ""),
            payload={
                "grooming": grooming_payload,
                "trigger_state": metadata["trigger_state"],
                "jira_enrichment": {"ticket_ids": [], "fetched": False, "missing": []},
            },
        )
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "jira_enrichment": {"ticket_ids": [], "fetched": False, "missing": []},
            "grooming": grooming_payload.get("schema") or {},
            "trigger_state": {
                "status": metadata["trigger_state"],
                "recommendation": "ready" if grooming_payload.get("is_complete") else "needs_input",
            },
            "user_message": user_message,
        }

    ticket_ids = _extract_ticket_ids(prompt)
    if not ticket_ids:
        assistant_text = _respond_with_llm(prompt, selected_model)
        metadata["trigger_state"] = "draft"
        metadata["model"] = selected_model
        metadata["client_context"] = payload.client_context or metadata.get("client_context") or {}
        get_history_store().update_chat_session(
            session_id,
            mode="support",
            selected_model=selected_model,
            updated_at=_utc_iso_now(),
            metadata=metadata,
        )

        assistant_message = _append_chat_message(
            session_id=session_id,
            role="assistant",
            kind="text",
            content=assistant_text,
            payload={
                "jira_enrichment": {"ticket_ids": [], "fetched": False, "missing": []},
                "trigger_state": metadata["trigger_state"],
            },
        )
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "jira_enrichment": {"ticket_ids": [], "fetched": False, "missing": []},
            "grooming": metadata.get("grooming_state") or {},
            "trigger_state": {"status": metadata["trigger_state"], "recommendation": "collect_requirements"},
            "user_message": user_message,
        }

    issues: list[dict] = []
    missing: list[str] = []
    for ticket_id in ticket_ids:
        try:
            issues.append(jira_service.get_issue(ticket_id))
        except Exception:  # noqa: BLE001
            missing.append(ticket_id)

    if not issues:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "JIRA_ENRICHMENT_FAILED",
                "message": "No Jira tickets could be enriched from this prompt",
                "details": {"ticket_ids": ticket_ids},
            },
        )

    seed_state = _derive_jira_seed_state(prompt, issues, metadata.get("grooming_state") or {})
    grooming_result = _build_grooming_response(prompt, seed_state)
    grooming_payload = grooming_result.get("grooming") or {}
    schema = grooming_payload.get("schema") or {}
    state = _normalize_grooming_state({**schema, "pending_field": grooming_payload.get("pending_field")})

    valid_ticket_ids = [str(issue.get("key") or "").upper() for issue in issues if issue.get("key")]
    metadata["grooming_state"] = state
    metadata["trigger_state"] = "ready_to_trigger" if grooming_payload.get("is_complete") else "grooming"
    metadata["last_ticket_ids"] = valid_ticket_ids
    metadata["model"] = selected_model
    metadata["client_context"] = payload.client_context or metadata.get("client_context") or {}

    get_history_store().update_chat_session(
        session_id,
        mode="support",
        selected_model=selected_model,
        updated_at=_utc_iso_now(),
        metadata=metadata,
    )

    jira_enrichment = {
        "ticket_ids": valid_ticket_ids,
        "fetched": True,
        "missing": missing,
    }
    assistant_text = (
        f"I fetched Jira details for {', '.join(valid_ticket_ids)} and drafted grooming output. "
        "Review and continue refinement before trigger."
    )
    assistant_message = _append_chat_message(
        session_id=session_id,
        role="assistant",
        kind="text",
        content=assistant_text,
        payload={
            "jira_enrichment": jira_enrichment,
            "grooming": grooming_payload,
            "trigger_state": metadata["trigger_state"],
        },
    )

    return {
        "session_id": session_id,
        "assistant_message": assistant_message,
        "jira_enrichment": jira_enrichment,
        "grooming": grooming_payload.get("schema") or {},
        "trigger_state": {
            "status": metadata["trigger_state"],
            "recommendation": "ready" if grooming_payload.get("is_complete") else "needs_input",
        },
        "user_message": user_message,
    }


@router.post("/chat/sessions/{session_id}/prepare-trigger")
def prepare_chat_session_trigger(
    session_id: str,
    payload: ChatPrepareTriggerRequest,
    _user: dict = Depends(require_run_permission),
):
    session = _chat_session_or_404(session_id)
    _ensure_open_session(session)
    metadata = _session_metadata(session)

    state = _normalize_grooming_state(metadata.get("grooming_state") or {})
    missing = _missing_grooming_fields(state)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "GROOMING_SCHEMA_INVALID",
                "message": "Grooming data is incomplete",
                "details": {"missing_fields": missing},
            },
        )

    client_context = metadata.get("client_context") if isinstance(metadata.get("client_context"), dict) else {}
    repository = payload.repository or client_context.get("active_repository") or client_context.get("repository")
    if not repository:
        raise HTTPException(status_code=422, detail="Repository is required before trigger preparation")

    base_branch = payload.base_branch or client_context.get("active_branch") or client_context.get("base_branch") or "development"
    reviewer = payload.reviewer if payload.reviewer is not None else client_context.get("reviewer")
    selected_agent = payload.selected_agent or client_context.get("selected_agent") or "SWE"
    selected_model = payload.selected_model or metadata.get("model") or session.get("selected_model")
    ticket_ids = metadata.get("last_ticket_ids") if isinstance(metadata.get("last_ticket_ids"), list) else []
    jira_ticket_id = str(ticket_ids[0]) if ticket_ids else None

    run_payload, jira_prefill, template, rationale = _build_orchestration_payload_from_grooming(
        state,
        repository=repository,
        base_branch=base_branch,
        reviewer=reviewer,
        selected_agent=selected_agent,
        selected_model=selected_model,
        jira_ticket_id=jira_ticket_id,
    )

    metadata["prepared_payload"] = run_payload
    metadata["trigger_state"] = "awaiting_confirmation"
    metadata["recommended_template"] = template
    metadata["recommendation_rationale"] = rationale
    metadata["jira_prefill"] = jira_prefill
    metadata["model"] = selected_model

    get_history_store().update_chat_session(
        session_id,
        selected_model=selected_model,
        updated_at=_utc_iso_now(),
        metadata=metadata,
    )

    assistant_message = _append_chat_message(
        session_id=session_id,
        role="assistant",
        kind="text",
        content="Trigger payload prepared. Review values and confirm to launch workflow.",
        payload={
            "orchestration_payload": run_payload,
            "trigger_state": metadata["trigger_state"],
            "recommended_template": template,
            "recommendation_rationale": rationale,
            "jira_prefill": jira_prefill,
        },
    )

    return {
        "session_id": session_id,
        "assistant_message": assistant_message,
        "orchestration_payload": run_payload,
        "recommended_template": template,
        "recommendation_rationale": rationale,
        "jira_prefill": jira_prefill,
        "trigger_state": {"status": metadata["trigger_state"], "recommendation": "ready"},
    }


@router.post("/chat/sessions/{session_id}/confirm-trigger")
def confirm_chat_session_trigger(
    session_id: str,
    payload: ChatConfirmTriggerRequest,
    _user: dict = Depends(require_run_permission),
):
    session = _chat_session_or_404(session_id)
    _ensure_open_session(session)
    metadata = _session_metadata(session)

    if not payload.confirm:
        raise HTTPException(
            status_code=422,
            detail={"code": "TRIGGER_CONFIRMATION_REQUIRED", "message": "confirm=true is required"},
        )

    prepared_payload = metadata.get("prepared_payload")
    if not isinstance(prepared_payload, dict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "TRIGGER_CONFIRMATION_REQUIRED",
                "message": "Prepare trigger payload before confirmation",
            },
        )

    if metadata.get("trigger_state") == "triggered":
        raise HTTPException(
            status_code=409,
            detail={"code": "TRIGGER_ALREADY_CONFIRMED", "message": "Session already triggered"},
        )

    trigger_confirmation_id = f"confirm-{uuid4().hex}"
    queued = enqueue_orchestration(
        OrchestrateRequest(**prepared_payload),
        request_context={
            "trigger_source": "chat_session",
            "chat_session_id": session_id,
            "chat_confirmation_id": trigger_confirmation_id,
            "idempotency_key": payload.idempotency_key,
        },
    )

    metadata["trigger_state"] = "triggered"
    metadata["queued_job"] = queued
    metadata["trigger_confirmation_id"] = trigger_confirmation_id
    get_history_store().update_chat_session(
        session_id,
        updated_at=_utc_iso_now(),
        metadata=metadata,
    )

    _append_chat_message(
        session_id=session_id,
        role="assistant",
        kind="text",
        content=f"Confirmed. Workflow queued with job {queued['job_id']}.",
        payload={"queued_job": queued, "trigger_confirmation_id": trigger_confirmation_id},
    )

    return {
        "session_id": session_id,
        "trigger_confirmation_id": trigger_confirmation_id,
        "job": {"job_id": queued["job_id"], "status": queued["status"]},
    }


@router.delete("/chat/sessions/{session_id}")
def archive_chat_session(session_id: str, _user: dict = Depends(require_run_permission)):
    _chat_session_or_404(session_id)
    archived = get_history_store().archive_chat_session(session_id, _utc_iso_now())
    if not archived:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return {"session_id": session_id, "status": "closed"}
