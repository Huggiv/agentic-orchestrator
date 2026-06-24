"""Agentic orchestration flow using GitHub CLI and standalone Copilot CLI."""

from __future__ import annotations

import base64
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


def _estimate_usage(ai_credits: int, output_chars: int) -> dict:
    """Estimate AI credit usage.

    Each Copilot CLI request counts as 1 AI credit.  We track request count in
    ``ai_credits`` (passed as request count) and output volume in
    ``output_chars`` just for informational purposes.  Cost formula:
        1 AI Credit = 0.01 USD
    """
    ai_credits_used = ai_credits
    usd_per_credit = 0.01
    estimated_cost = ai_credits_used * usd_per_credit
    return {
        "ai_credits_used": ai_credits_used,
        "output_chars": output_chars,
        "estimated_cost_usd": round(estimated_cost, 4),
    }


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


def _run_copilot_prompt(prompt: str, cwd: str, env: dict[str, str]) -> str:
    """Run Copilot CLI in non-interactive mode with full tool permissions."""
    try:
        return _run(
            [
                "copilot",
                "--allow-all-tools",
                "--allow-all-paths",
                "--allow-all-urls",
                "--no-ask-user",
                "--no-color",
                "-p",
                prompt,
            ],
            cwd=cwd,
            env=env,
        )
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


_REPO_BASE_DIR = Path(os.environ.get("OTF_REPO_BASE_DIR", "/tmp/otf-tmp-repos"))
_NOTES_DIR_NAME = ".otf_agentic"
_COMMIT_EXCLUDE_PATHS = [":(exclude).otf_agentic/**", ":(exclude).otf-agentic/**"]
_REPO_INSTRUCTION_MAX_FILE_CHARS = 2_500
_REPO_INSTRUCTION_MAX_TOTAL_CHARS = 12_000


def _parse_scaled_number(raw: str | None) -> int | None:
    if not raw:
        return None
    value = raw.strip().lower().replace(",", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([km]?)", value)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1
    if suffix == "k":
        multiplier = 1_000
    elif suffix == "m":
        multiplier = 1_000_000
    return int(round(number * multiplier))


def _parse_duration_seconds(raw: str | None) -> int | None:
    if not raw:
        return None
    text = raw.strip().lower()
    total = 0
    found = False
    for amount, unit in re.findall(r"(\d+)\s*([hms])", text):
        found = True
        num = int(amount)
        if unit == "h":
            total += num * 3600
        elif unit == "m":
            total += num * 60
        else:
            total += num
    return total if found else None


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


def _parse_copilot_usage(output: str) -> dict:
    parsed: dict = {}
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue

        # Copilot CLI usage summaries can include decorative box-drawing prefixes.
        changes_match = re.search(r"Changes\s+\+(\d+)\s+-(\d+)", text, re.IGNORECASE)
        if changes_match:
            parsed["changes_added"] = int(changes_match.group(1))
            parsed["changes_removed"] = int(changes_match.group(2))
            continue

        credit_match = re.search(r"AI Credits\s+([0-9]+(?:\.[0-9]+)?)\s+\(([^)]+)\)", text, re.IGNORECASE)
        if credit_match:
            parsed["ai_credits_used"] = float(credit_match.group(1))
            parsed["ai_elapsed_text"] = credit_match.group(2).strip()
            parsed["ai_elapsed_seconds"] = _parse_duration_seconds(parsed["ai_elapsed_text"])
            continue

        token_match = re.search(
            r"Tokens\s+↑\s+([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+\(([^)]*)\)\s+•\s+↓\s+([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+\(([^)]*)\)",
            text,
            re.IGNORECASE,
        )
        if token_match:
            input_total = _parse_scaled_number(token_match.group(1))
            input_detail = token_match.group(2)
            output_total = _parse_scaled_number(token_match.group(3))
            output_detail = token_match.group(4)

            cached_match = re.search(r"([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+cached\b", input_detail)
            written_match = re.search(r"([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+written\b", input_detail)
            reasoning_match = re.search(r"([0-9]+(?:\.[0-9]+)?[kKmM]?)\s+reasoning\b", output_detail)

            parsed["tokens_input_total"] = input_total
            parsed["tokens_input_cached"] = _parse_scaled_number(cached_match.group(1)) if cached_match else None
            parsed["tokens_input_written"] = _parse_scaled_number(written_match.group(1)) if written_match else None
            parsed["tokens_output_total"] = output_total
            parsed["tokens_output_reasoning"] = _parse_scaled_number(reasoning_match.group(1)) if reasoning_match else None
            continue

    return parsed


def _safe_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


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


def _merge_usage(
    estimated_usage: dict,
    snapshots: list[dict],
    changes_override: dict[str, int] | None = None,
) -> dict:
    usage = dict(estimated_usage)

    changes_added = 0
    changes_removed = 0
    credits_used = 0.0
    credits_found = False
    elapsed_seconds = 0
    elapsed_found = False
    tokens_input_total = 0
    tokens_input_cached = 0
    tokens_input_written = 0
    tokens_output_total = 0
    tokens_output_reasoning = 0
    tokens_found = False

    for snap in snapshots:
        if "changes_added" in snap:
            changes_added += int(snap.get("changes_added") or 0)
        if "changes_removed" in snap:
            changes_removed += int(snap.get("changes_removed") or 0)

        if "ai_credits_used" in snap:
            credits_used += float(snap.get("ai_credits_used") or 0)
            credits_found = True

        if snap.get("ai_elapsed_seconds") is not None:
            elapsed_seconds += int(snap["ai_elapsed_seconds"])
            elapsed_found = True

        token_values = (
            snap.get("tokens_input_total"),
            snap.get("tokens_input_cached"),
            snap.get("tokens_input_written"),
            snap.get("tokens_output_total"),
            snap.get("tokens_output_reasoning"),
        )
        if any(value is not None for value in token_values):
            tokens_found = True
            tokens_input_total += int(snap.get("tokens_input_total") or 0)
            tokens_input_cached += int(snap.get("tokens_input_cached") or 0)
            tokens_input_written += int(snap.get("tokens_input_written") or 0)
            tokens_output_total += int(snap.get("tokens_output_total") or 0)
            tokens_output_reasoning += int(snap.get("tokens_output_reasoning") or 0)

    use_changes_override = bool(changes_override) and any(
        int((changes_override or {}).get(key, 0)) > 0 for key in ("added", "removed")
    )
    usage["changes"] = (changes_override or {}) if use_changes_override else {
        "added": changes_added,
        "removed": changes_removed,
    }

    input_cost_rate = _safe_float(os.environ.get("COPILOT_INPUT_COST_PER_1K"), 0.003)
    output_cost_rate = _safe_float(os.environ.get("COPILOT_OUTPUT_COST_PER_1K"), 0.015)

    if credits_found:
        usage["ai_credits_used"] = round(credits_used, 2)
        usage["estimated_cost_usd"] = round(credits_used * 0.01, 4)

    usage["ai"] = {
        "elapsed_seconds": elapsed_seconds if elapsed_found else None,
        "elapsed_text": _format_duration(elapsed_seconds) if elapsed_found else None,
    }

    usage["tokens"] = {
        "found": tokens_found,
        "input_total": tokens_input_total if tokens_found else None,
        "input_cached": tokens_input_cached if tokens_found else None,
        "input_written": tokens_input_written if tokens_found else None,
        "output_total": tokens_output_total if tokens_found else None,
        "output_reasoning": tokens_output_reasoning if tokens_found else None,
    }

    if tokens_found:
        token_cost = (tokens_input_total / 1000.0) * input_cost_rate + (tokens_output_total / 1000.0) * output_cost_rate
        usage["tokens"]["estimated_cost_usd"] = round(token_cost, 4)
        if "estimated_cost_usd" not in usage:
            usage["estimated_cost_usd"] = round(token_cost, 4)

    return usage


def run_orchestration(
    jira_ticket_id: str,
    repository: str,
    base_branch: str,
    reviewer: str | None,
    commit_message: str,
    change_plan: list[str],
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    env = _prepare_env()
    clone_url, repo_slug = _normalize_repo(repository)
    token = env["GITHUB_TOKEN"]
    git_auth_header = _git_auth_header(token)
    branch_name = _build_branch_name(jira_ticket_id)
    steps: list[StepResult] = []
    copilot_notes: list[str] = []
    copilot_requests = 0
    total_output_chars = 0
    copilot_usage_snapshots: list[dict] = []
    repo_instructions = []

    run_id = f"otf-agentic-{uuid.uuid4().hex[:8]}"
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
    full_prompt = (
        f"Repository: {repo_slug}\n"
        f"Jira Ticket: {jira_ticket_id}\n"
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
    output = _run_copilot_prompt(full_prompt, cwd=repo_path, env=env)
    copilot_requests += 1
    total_output_chars += len(output)
    copilot_usage_snapshots.append(_parse_copilot_usage(output))
    if output:
        copilot_notes.append(output)

    # Persist generated instructions so the flow produces concrete repo changes.
    notes_dir = Path(repo_path) / _NOTES_DIR_NAME
    notes_dir.mkdir(exist_ok=True)
    notes_file = notes_dir / f"{jira_ticket_id.lower()}-implementation.md"
    dod_lines = [f"- {point}" for point in dod_points] or ["- Not explicitly provided"]
    suggestion_lines = [f"### Suggestion {idx + 1}\n{note}\n" for idx, note in enumerate(copilot_notes)]
    notes_content = "\n".join(
        [
            f"# Agentic Implementation Notes for {jira_ticket_id}",
            "",
            f"Repository: {repo_slug}",
            f"Summary: {summary}",
            "",
            "## DoD",
            *dod_lines,
            "",
            "## Copilot Suggestions",
            *suggestion_lines,
        ]
    )
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
        _run(["git", "config", "user.email", os.environ.get("GIT_AUTHOR_EMAIL", "otf-bot@example.com")], cwd=repo_path, env=env)
        _run(["git", "config", "user.name", os.environ.get("GIT_AUTHOR_NAME", "OTF Agentic Bot")], cwd=repo_path, env=env)
        _run(["git", "commit", "-m", commit_message], cwd=repo_path, env=env)
        _emit_progress(progress_callback, "commit_changes", "success", commit_message)
        steps.append(StepResult(name="commit_changes", status="success", details=commit_message))
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

    usage = _merge_usage(
        _estimate_usage(copilot_requests, total_output_chars),
        copilot_usage_snapshots,
        changes_override=changes_summary,
    )

    return {
        "branch_name": branch_name,
        "pull_request_url": pr_url,
        "steps": [s.__dict__ for s in steps],
        "copilot_notes": copilot_notes,
        "artifacts": note_artifacts,
        "usage": usage,
    }
