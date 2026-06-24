"""Agentic orchestration flow using GitHub CLI and standalone Copilot CLI."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from app.jira import service as jira_service


class OrchestrationError(Exception):
    """Raised when orchestration execution fails."""


@dataclass
class StepResult:
    name: str
    status: str
    details: str | None = None


COPILOT_AUTH_ERROR = (
    "Copilot CLI authentication failed. Configure COPILOT_GITHUB_TOKEN with a valid "
    "token that has the 'Copilot Requests' permission, or log in with 'copilot login' "
    "on the host and restart the backend. The current GITHUB_TOKEN can still be used "
    "for git and PR APIs, but Copilot CLI should use a dedicated Copilot-capable token."
)

DEFAULT_COPILOT_AGENT = "SWE"
_SPECIALIST_AGENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "GitHub Actions Expert",
        (
            "github action",
            "github actions",
            "workflow",
            "ci/cd",
            "pipeline",
            ".github/workflows",
            "action.yml",
            "action yaml",
        ),
    ),
    (
        "Expert React Frontend Engineer",
        (
            "frontend",
            "react",
            "vite",
            "component",
            "jsx",
            "css",
            "ui",
            "ux",
        ),
    ),
    (
        "DevOps Expert",
        (
            "devops",
            "kubernetes",
            "k8s",
            "helm",
            "terraform",
            "docker",
            "deployment",
            "infrastructure",
            "sre",
            "observability",
        ),
    ),
    (
        "Project Documenter",
        (
            "documentation",
            "document",
            "readme",
            "architecture diagram",
            "plantuml",
            "docx",
        ),
    ),
]


def _run(cmd: list[str], cwd: str, env: dict[str, str]) -> str:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"Command failed: {' '.join(shlex.quote(c) for c in cmd)}"
        raise OrchestrationError(detail)
    return (proc.stdout or "").strip()


def _build_branch_name(jira_ticket_id: str) -> str:
    normalized = jira_ticket_id.lower().replace("_", "-")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"feature/{normalized}-{stamp}"


def _normalize_repo(repository: str) -> tuple[str, str]:
    value = repository.strip()
    if not value:
        raise OrchestrationError("Repository is required")

    if value.startswith("https://") or value.startswith("git@"):
        clone_url = value
        slug = value.split(":")[-1] if value.startswith("git@") else value.split("github.com/")[-1]
        slug = slug.removesuffix(".git").strip("/")
        if "/" not in slug:
            raise OrchestrationError("Repository URL must include owner/repository")
        return clone_url, slug

    if re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        return f"https://github.com/{value}.git", value

    raise OrchestrationError("Repository must be owner/repo or a GitHub clone URL")


def _extract_dod_points(description: str) -> list[str]:
    if not description:
        return []
    lines = description.splitlines()
    dod_start = None
    for idx, line in enumerate(lines):
        if "dod" in line.lower().replace(" ", ""):
            dod_start = idx
            break
    if dod_start is None:
        return []

    points: list[str] = []
    for raw in lines[dod_start + 1 :]:
        line = raw.strip()
        if not line:
            if points:
                break
            continue
        if line.startswith(("*", "-", "#")):
            points.append(line.lstrip("*-# ").strip())
        elif points:
            break
    return points


def _emit_progress(
    cb: Callable[[dict], None] | None,
    name: str,
    status: str,
    details: str | None = None,
) -> None:
    if not cb:
        return
    cb(
        {
            "name": name,
            "status": status,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


def _prepare_env() -> dict[str, str]:
    git_token = os.environ.get("GITHUB_TOKEN")
    if not git_token:
        raise OrchestrationError("Missing required env var: GITHUB_TOKEN")
    env = os.environ.copy()
    env["GITHUB_TOKEN"] = git_token

    copilot_token = os.environ.get("COPILOT_GITHUB_TOKEN")
    if copilot_token:
        env["COPILOT_GITHUB_TOKEN"] = copilot_token
        return env

    gh_oauth_token = _read_gh_auth_token()
    if gh_oauth_token:
        env["COPILOT_GITHUB_TOKEN"] = gh_oauth_token
        return env

    if os.environ.get("GH_TOKEN"):
        env["COPILOT_GITHUB_TOKEN"] = os.environ["GH_TOKEN"]
        return env

    env["COPILOT_GITHUB_TOKEN"] = git_token
    return env


def _read_gh_auth_token() -> str | None:
    """Read the OAuth token stored by gh for Copilot CLI use in headless runs."""

    sanitized_env = os.environ.copy()
    sanitized_env.pop("COPILOT_GITHUB_TOKEN", None)
    sanitized_env.pop("GH_TOKEN", None)
    sanitized_env.pop("GITHUB_TOKEN", None)
    proc = subprocess.run(
        ["gh", "auth", "token"],
        capture_output=True,
        text=True,
        check=False,
        env=sanitized_env,
    )
    if proc.returncode != 0:
        return None
    token = (proc.stdout or "").strip()
    return token or None


def _copilot_auth_source(env: dict[str, str]) -> str:
    if os.environ.get("COPILOT_GITHUB_TOKEN"):
        return "COPILOT_GITHUB_TOKEN"
    if env.get("COPILOT_GITHUB_TOKEN") and env.get("COPILOT_GITHUB_TOKEN") != env.get("GITHUB_TOKEN"):
        return "gh auth token"
    if os.environ.get("GH_TOKEN"):
        return "GH_TOKEN"
    return "GITHUB_TOKEN"


def _select_copilot_agent(issue: dict, change_plan: list[str]) -> tuple[str, str]:
    labels = issue.get("labels") if isinstance(issue.get("labels"), list) else []
    corpus = "\n".join(
        [
            str(issue.get("summary", "")),
            str(issue.get("description", "")),
            str(issue.get("type", "")),
            str(issue.get("status", "")),
            str(issue.get("priority", "")),
            "\n".join(str(label) for label in labels),
            "\n".join(change_plan),
        ]
    ).lower()

    for agent_name, keywords in _SPECIALIST_AGENT_RULES:
        for keyword in keywords:
            if keyword in corpus:
                return agent_name, f"Matched Jira requirement keyword: {keyword}"

    return DEFAULT_COPILOT_AGENT, "Default agent for code implementation"


def _run_copilot_prompt(prompt: str, cwd: str, env: dict[str, str], agent_name: str, model: str | None = None) -> str:
    """Run Copilot CLI in non-interactive mode with full tool permissions."""
    cmd = [
        "copilot",
        "--agent",
        agent_name,
        "--allow-all-tools",
        "--allow-all-paths",
        "--allow-all-urls",
        "--no-ask-user",
        "--no-color",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.extend(["-p", prompt])
    try:
        return _run(cmd, cwd=cwd, env=env)
    except OrchestrationError as exc:
        detail = str(exc)
        auth_markers = (
            "Authentication failed",
            "Your GitHub token may be invalid",
            "Copilot Requests",
            "copilot login",
        )
        if any(marker in detail for marker in auth_markers):
            raise OrchestrationError(COPILOT_AUTH_ERROR) from exc
        raise


def _git_auth_header(token: str) -> str:
    encoded = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return f"AUTHORIZATION: basic {encoded}"


_REPO_BASE_DIR = Path(os.environ.get("AGENT_FLOW_REPO_BASE_DIR", "/tmp/agent_flow-tmp-repos"))
_NOTES_DIR_NAME = ".agent_flow_agentic"
_COMMIT_EXCLUDE_PATHS = [":(exclude).agent_flow_agentic/**", ":(exclude).agent_flow-agentic/**"]
_REPO_INSTRUCTION_MAX_FILE_CHARS = 2_500
_REPO_INSTRUCTION_MAX_TOTAL_CHARS = 12_000
_NANO_AIU_PER_CREDIT = 1_000_000_000
_COPILOT_SESSION_STATE_DIR = Path(
    os.environ.get("COPILOT_SESSION_STATE_DIR", str(Path.home() / ".copilot" / "session-state"))
)


def _format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m {secs}s"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def _collect_change_stats(repo_path: str, env: dict[str, str]) -> dict[str, int]:
    output = _run(
        [
            "git",
            "diff",
            "--numstat",
            "--",
            ".",
            *_COMMIT_EXCLUDE_PATHS,
        ],
        cwd=repo_path,
        env=env,
    )

    added = 0
    removed = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])

    return {"added": added, "removed": removed}


def _count_commits_ahead(repo_path: str, env: dict[str, str], base_branch: str) -> int:
    output = _run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=repo_path,
        env=env,
    )
    try:
        return int(output.strip())
    except ValueError:
        return 0


def _collect_repo_instruction_context(repo_path: str) -> list[dict[str, str]]:
    github_dir = Path(repo_path) / ".github"
    if not github_dir.exists() or not github_dir.is_dir():
        return []

    candidates: list[Path] = []
    root_candidates = [
        github_dir / "copilot-instructions.md",
        github_dir / "instructions.md",
        github_dir / "AGENTS.md",
    ]
    candidates.extend([path for path in root_candidates if path.is_file()])
    candidates.extend(sorted(path for path in github_dir.glob("*.instructions.md") if path.is_file()))

    for folder in ["agents", "prompts"]:
        sub_dir = github_dir / folder
        if sub_dir.exists() and sub_dir.is_dir():
            candidates.extend(sorted(path for path in sub_dir.rglob("*.md") if path.is_file()))

    unique_files: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique_files.append(path)

    artifacts: list[dict[str, str]] = []
    total_chars = 0
    for path in unique_files:
        rel_path = str(path.relative_to(repo_path))
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")
        if total_chars >= _REPO_INSTRUCTION_MAX_TOTAL_CHARS:
            break
        remaining = _REPO_INSTRUCTION_MAX_TOTAL_CHARS - total_chars
        sliced = content[: min(_REPO_INSTRUCTION_MAX_FILE_CHARS, remaining)]
        artifacts.append({"path": rel_path, "content": sliced})
        total_chars += len(sliced)

    return artifacts


def _safe_int(raw: object, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _extract_copilot_session_id(output: str) -> str | None:
    match = re.search(r"--resume=([0-9a-fA-F-]{36})", output)
    if not match:
        return None
    return match.group(1)


def _read_shutdown_event(events_file: Path) -> dict | None:
    if not events_file.exists() or not events_file.is_file():
        return None

    shutdown_data: dict | None = None
    with events_file.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if payload.get("type") != "session.shutdown":
                continue
            data = payload.get("data")
            if isinstance(data, dict):
                shutdown_data = data

    return shutdown_data


def _load_shutdown_events(session_ids: list[str]) -> list[dict]:
    if not _COPILOT_SESSION_STATE_DIR.exists() or not _COPILOT_SESSION_STATE_DIR.is_dir():
        return []

    unique_ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    events: list[dict] = []

    if unique_ids:
        candidates = [_COPILOT_SESSION_STATE_DIR / sid / "events.jsonl" for sid in unique_ids]
    else:
        candidates = sorted(
            (path / "events.jsonl" for path in _COPILOT_SESSION_STATE_DIR.iterdir() if path.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        candidates = candidates[:1]

    for events_file in candidates:
        shutdown = _read_shutdown_event(events_file)
        if shutdown:
            events.append(shutdown)

    return events


def _build_usage_from_session_logs(session_ids: list[str], changes_override: dict[str, int] | None = None) -> dict:
    shutdown_events = _load_shutdown_events(session_ids)
    session_log_found = bool(shutdown_events)

    total_nano_aiu = 0
    total_api_duration_ms = 0
    token_input = 0
    token_output = 0
    token_cache_read = 0
    token_cache_write = 0
    changes_added = 0
    changes_removed = 0

    for event in shutdown_events:
        total_nano_aiu += _safe_int(event.get("totalNanoAiu"))
        total_api_duration_ms += _safe_int(event.get("totalApiDurationMs"))

        token_details = event.get("tokenDetails") if isinstance(event.get("tokenDetails"), dict) else {}
        token_input += _safe_int(((token_details.get("input") or {}).get("tokenCount") if token_details else 0))
        token_output += _safe_int(((token_details.get("output") or {}).get("tokenCount") if token_details else 0))
        token_cache_read += _safe_int(((token_details.get("cache_read") or {}).get("tokenCount") if token_details else 0))
        token_cache_write += _safe_int(((token_details.get("cache_write") or {}).get("tokenCount") if token_details else 0))

        code_changes = event.get("codeChanges") if isinstance(event.get("codeChanges"), dict) else {}
        changes_added += _safe_int(code_changes.get("linesAdded"))
        changes_removed += _safe_int(code_changes.get("linesRemoved"))

    cached_tokens = token_cache_read + token_cache_write
    total_tokens = token_input + token_output + cached_tokens
    duration_seconds = round(total_api_duration_ms / 1000) if total_api_duration_ms > 0 else None

    use_changes_override = bool(changes_override) and any(
        int((changes_override or {}).get(key, 0)) > 0 for key in ("added", "removed")
    )
    changes = (changes_override or {}) if use_changes_override else {
        "added": changes_added,
        "removed": changes_removed,
    }

    ai_credits_used = total_nano_aiu / _NANO_AIU_PER_CREDIT
    usage = {
        "source": "copilot_session_logs",
        "session_log_found": session_log_found,
        "session_ids": list(dict.fromkeys(session_ids)),
        "total_nano_aiu": total_nano_aiu,
        "changes": changes,
        "ai_credits_used": round(ai_credits_used, 4),
        "estimated_cost_usd": round(ai_credits_used * 0.01, 4),
        "ai": {
            "duration_seconds": duration_seconds,
            "duration_text": _format_duration(duration_seconds),
            "total_api_duration_ms": total_api_duration_ms or None,
        },
        "tokens": {
            "found": session_log_found,
            "total": total_tokens if session_log_found else None,
            "input": token_input if session_log_found else None,
            "output": token_output if session_log_found else None,
            "cached": cached_tokens if session_log_found else None,
            "cache_read": token_cache_read if session_log_found else None,
            "cache_write": token_cache_write if session_log_found else None,
        },
    }
    return usage


def run_orchestration(
    jira_ticket_id: str,
    repository: str,
    base_branch: str,
    reviewer: str | None,
    commit_message: str,
    change_plan: list[str],
    selected_agent: str | None = None,
    selected_model: str | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    env = _prepare_env()
    clone_url, repo_slug = _normalize_repo(repository)
    token = env["GITHUB_TOKEN"]
    git_auth_header = _git_auth_header(token)
    branch_name = _build_branch_name(jira_ticket_id)
    steps: list[StepResult] = []
    copilot_notes: list[str] = []
    copilot_session_ids: list[str] = []
    repo_instructions = []

    run_id = f"agent_flow-agentic-{uuid.uuid4().hex[:8]}"
    _REPO_BASE_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = str(_REPO_BASE_DIR / run_id)
    os.makedirs(temp_dir, exist_ok=True)
    repo_path = str(Path(temp_dir) / "repo")

    _emit_progress(progress_callback, "clone_repository", "running", clone_url)
    _run(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader={git_auth_header}",
            "clone",
            clone_url,
            repo_path,
        ],
        cwd=temp_dir,
        env=env,
    )
    _emit_progress(progress_callback, "clone_repository", "success", repo_path)
    steps.append(StepResult(name="clone_repository", status="success", details=repo_path))

    # Normalize origin explicitly to target repo slug.
    _run(["git", "remote", "set-url", "origin", clone_url], cwd=repo_path, env=env)

    _emit_progress(progress_callback, "read_repo_instructions", "running")
    repo_instructions = _collect_repo_instruction_context(repo_path)
    _emit_progress(
        progress_callback,
        "read_repo_instructions",
        "success",
        f"{len(repo_instructions)} file(s)",
    )
    steps.append(
        StepResult(
            name="read_repo_instructions",
            status="success",
            details=f"{len(repo_instructions)} file(s)",
        )
    )

    _emit_progress(progress_callback, "auth_setup", "running")
    _run(["gh", "--version"], cwd=repo_path, env=env)
    _run(["gh", "auth", "status"], cwd=repo_path, env=env)
    _run(["copilot", "--version"], cwd=repo_path, env=env)
    copilot_source = _copilot_auth_source(env)
    _emit_progress(progress_callback, "auth_setup", "success", copilot_source)
    steps.append(StepResult(name="auth_setup", status="success", details=copilot_source))

    _emit_progress(progress_callback, "prepare_branch", "running", base_branch)
    _run(["git", "checkout", base_branch], cwd=repo_path, env=env)
    _run(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader={git_auth_header}",
            "pull",
            "--ff-only",
            "origin",
            base_branch,
        ],
        cwd=repo_path,
        env=env,
    )
    _run(["git", "checkout", "-b", branch_name], cwd=repo_path, env=env)
    _emit_progress(progress_callback, "prepare_branch", "success", branch_name)
    steps.append(StepResult(name="create_and_checkout_branch", status="success", details=branch_name))

    _emit_progress(progress_callback, "read_jira", "running", jira_ticket_id)
    issue = jira_service.get_issue(jira_ticket_id)
    summary = issue.get("summary", "")
    description = issue.get("description") or ""
    dod_points = _extract_dod_points(description)
    _emit_progress(progress_callback, "read_jira", "success", summary or jira_ticket_id)
    steps.append(StepResult(name="read_jira_issue", status="success", details=summary or jira_ticket_id))

    if selected_agent and selected_agent.strip():
        selected_agent = selected_agent.strip()
        selected_agent_reason = "Selected by user input"
    else:
        selected_agent, selected_agent_reason = _select_copilot_agent(issue, change_plan)
    _emit_progress(progress_callback, "select_copilot_agent", "success", f"{selected_agent} ({selected_agent_reason})")
    steps.append(
        StepResult(
            name="select_copilot_agent",
            status="success",
            details=f"{selected_agent}: {selected_agent_reason}",
        )
    )

    effective_plan = change_plan or [
        "Implement code changes for Jira ticket description",
        "Add tests or validation updates for DoD",
        "Update documentation for behavior changes",
    ]

    _emit_progress(progress_callback, "agentic_implementation", "running")
    # Build a single rich prompt with the full JIRA context so Copilot CLI can
    # autonomously perform implementation and validation.
    dod_text = "\n".join(f"- {p}" for p in dod_points) if dod_points else "Not provided"
    plan_text = "\n".join(f"- {item}" for item in effective_plan)
    repo_instruction_text = "\n\n".join(
        f"### {artifact['path']}\n{artifact['content']}" for artifact in repo_instructions
    )
    
    # Store Jira context for implementation.md
    jira_context = {
        "ticket_id": jira_ticket_id,
        "summary": summary,
        "description": description,
        "dod_points": dod_points,
        "type": issue.get("type", ""),
        "status": issue.get("status", ""),
        "priority": issue.get("priority", ""),
    }
    
    full_prompt = (
        f"Repository: {repo_slug}\n"
        f"Jira Ticket: {jira_ticket_id}\n"
        f"Selected Copilot Agent: {selected_agent}\n"
        f"Summary: {summary}\n"
        f"Description:\n{description}\n\n"
        f"Definition of Done:\n{dod_text}\n\n"
        f"Implementation Plan:\n{plan_text}\n\n"
        f"Repository Instructions From .github:\n{repo_instruction_text or 'None found'}\n\n"
        "Act as an autonomous coding agent and make code changes in this repository "
        "to satisfy the Jira ticket and DoD. Use Python and React standard coding "
        "guidelines, keep Sonar-friendly clean-code practices, and add or update tests "
        "to validate the implemented behavior. Run the relevant test commands, then "
        "summarize changed files, test outcomes, and any follow-up risks."
    )
    output = _run_copilot_prompt(full_prompt, cwd=repo_path, env=env, agent_name=selected_agent, model=selected_model)
    session_id = _extract_copilot_session_id(output)
    if session_id:
        copilot_session_ids.append(session_id)
    if output:
        copilot_notes.append(output)

    # Persist generated instructions so the flow produces concrete repo changes.
    notes_dir = Path(repo_path) / _NOTES_DIR_NAME
    notes_dir.mkdir(exist_ok=True)
    notes_file = notes_dir / f"{jira_ticket_id.lower()}-implementation.md"
    dod_lines = [f"- {point}" for point in dod_points] or ["- Not explicitly provided"]
    suggestion_lines = [f"### Suggestion {idx + 1}\n{note}\n" for idx, note in enumerate(copilot_notes)]
    
    # Build rich notes with full Jira context
    notes_sections = [
        f"# Agentic Implementation Notes for {jira_ticket_id}",
        "",
        "## Jira Issue Details",
        f"- **Ticket ID:** {jira_context['ticket_id']}",
        f"- **Type:** {jira_context['type']}",
        f"- **Status:** {jira_context['status']}",
        f"- **Priority:** {jira_context['priority']}",
        f"- **Summary:** {jira_context['summary']}",
        "",
        "## Description",
        jira_context['description'] or "*No description provided*",
        "",
        "## Definition of Done (DoD)",
        *dod_lines,
        "",
        "## Repository",
        repo_slug,
        "",
        "## Implementation Plan",
        plan_text,
        "",
        "## Selected Copilot Agent",
        f"- **Agent:** {selected_agent}",
        f"- **Selection Reason:** {selected_agent_reason}",
        "",
        "## Copilot Suggestions",
        *suggestion_lines,
    ]
    notes_content = "\n".join(notes_sections)
    notes_file.write_text(notes_content)

    note_artifacts = [
        {
            "path": f"{_NOTES_DIR_NAME}/{notes_file.name}",
            "content": notes_content,
        }
    ]

    _emit_progress(progress_callback, "agentic_implementation", "success")
    steps.append(StepResult(name="copilot_agentic_plan", status="success"))

    changes_summary = _collect_change_stats(repo_path, env)

    _emit_progress(progress_callback, "commit_changes", "running")
    _run(
        [
            "git",
            "add",
            "-A",
            "--",
            ".",
            *_COMMIT_EXCLUDE_PATHS,
        ],
        cwd=repo_path,
        env=env,
    )
    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=repo_path, env=env)
    if staged:
        _run(["git", "config", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "agent_flow-bot@example.com")], cwd=repo_path, env=env)
        _run(["git", "config", "user.name", os.environ.get("GIT_AUTHOR_NAME", "AGENT_FLOW Agentic Bot")], cwd=repo_path, env=env)
        _run(["git", "commit", "-m", commit_message], cwd=repo_path, env=env)
        _emit_progress(progress_callback, "commit_changes", "success", commit_message)
        steps.append(StepResult(name="commit_changes", status="success", details=commit_message))
    else:
        commits_ahead = _count_commits_ahead(repo_path, env, base_branch)
        if commits_ahead > 0:
            details = f"Branch already has {commits_ahead} commit(s) ahead of {base_branch}"
            _emit_progress(progress_callback, "commit_changes", "success", details)
            steps.append(StepResult(name="commit_changes", status="success", details=details))
        else:
            _emit_progress(progress_callback, "commit_changes", "skipped", "No file changes to commit")
            steps.append(StepResult(name="commit_changes", status="skipped", details="No file changes to commit"))

    _emit_progress(progress_callback, "push_branch", "running")
    _run(
        [
            "git",
            "-c",
            f"http.https://github.com/.extraheader={git_auth_header}",
            "push",
            "-u",
            "origin",
            branch_name,
        ],
        cwd=repo_path,
        env=env,
    )
    _emit_progress(progress_callback, "push_branch", "success", branch_name)
    steps.append(StepResult(name="push_branch", status="success", details=branch_name))

    _emit_progress(progress_callback, "create_pr", "running")
    pr_title = f"{jira_ticket_id}: {commit_message}"
    pr_body = "Automated agentic flow execution via backend orchestration."
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    pr_response = requests.post(
        f"https://api.github.com/repos/{repo_slug}/pulls",
        json={
            "title": pr_title,
            "head": branch_name,
            "base": base_branch,
            "body": pr_body,
        },
        headers=headers,
        timeout=60,
    )
    if pr_response.status_code not in (200, 201):
        detail = pr_response.text.strip() or f"Failed to create PR for {repo_slug}"
        raise OrchestrationError(detail)

    pr_payload = pr_response.json()
    pr_url = pr_payload.get("html_url")
    pr_number = pr_payload.get("number")
    if not pr_url:
        raise OrchestrationError("PR creation succeeded but no html_url was returned")

    if reviewer and pr_number:
        reviewer_response = requests.post(
            f"https://api.github.com/repos/{repo_slug}/pulls/{pr_number}/requested_reviewers",
            json={"reviewers": [reviewer]},
            headers=headers,
            timeout=60,
        )
        if reviewer_response.status_code not in (200, 201):
            detail = reviewer_response.text.strip() or f"Failed to request reviewer: {reviewer}"
            raise OrchestrationError(detail)

    _emit_progress(progress_callback, "create_pr", "success", pr_url)
    steps.append(StepResult(name="create_pr", status="success", details=pr_url))

    usage = _build_usage_from_session_logs(
        copilot_session_ids,
        changes_override=changes_summary,
    )

    return {
        "branch_name": branch_name,
        "pull_request_url": pr_url,
        "steps": [s.__dict__ for s in steps],
        "selected_agent": selected_agent,
        "copilot_notes": copilot_notes,
        "artifacts": note_artifacts,
        "usage": usage,
    }
