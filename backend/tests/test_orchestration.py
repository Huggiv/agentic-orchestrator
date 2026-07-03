import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.orchestration import (
    COPILOT_AUTH_ERROR,
    OrchestrationError,
    _build_usage_from_session_logs,
    _count_commits_ahead,
    _extract_copilot_session_id,
    _extract_pr_review_findings,
    _prepare_env,
    _run_copilot_prompt,
    run_orchestration,
    _select_copilot_agent,
)


def test_prepare_env_prefers_gh_auth_token_for_copilot(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "github_pat_repo_only")
    monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def fake_run(cmd, capture_output, text, check, env, cwd=None):
        assert cmd == ["gh", "auth", "token"]
        assert "GITHUB_TOKEN" not in env
        return SimpleNamespace(returncode=0, stdout="gho_oauth_token\n", stderr="")

    monkeypatch.setattr("app.orchestration.subprocess.run", fake_run)

    env = _prepare_env()

    assert env["GITHUB_TOKEN"] == "github_pat_repo_only"
    assert env["COPILOT_GITHUB_TOKEN"] == "gho_oauth_token"


def test_run_copilot_prompt_rewrites_auth_error(monkeypatch):
    def fake_run(cmd, cwd, env, cancellation_token=None):
        raise OrchestrationError(
            "Authentication failed. Your GitHub token may be invalid or missing Copilot Requests permission."
        )

    monkeypatch.setattr("app.orchestration._run", fake_run)

    with pytest.raises(OrchestrationError, match="Copilot CLI authentication failed") as exc_info:
        _run_copilot_prompt("hello", cwd="/tmp", env={"COPILOT_GITHUB_TOKEN": "token"}, agent_name="SWE")

    assert str(exc_info.value) == COPILOT_AUTH_ERROR


def test_run_copilot_prompt_includes_agent_flag(monkeypatch):
    captured = {}

    def fake_run(cmd, cwd, env, cancellation_token=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return "ok"

    monkeypatch.setattr("app.orchestration._run", fake_run)

    output = _run_copilot_prompt("hello", cwd="/tmp", env={"COPILOT_GITHUB_TOKEN": "token"}, agent_name="SWE")

    assert output == "ok"
    assert captured["cmd"][:3] == ["copilot", "--agent", "SWE"]


def test_select_copilot_agent_defaults_to_swe():
    agent, reason = _select_copilot_agent(
        {
            "summary": "Fix backend pagination bug",
            "description": "Adjust SQL query and API response structure",
            "type": "Bug",
            "labels": ["backend"],
        },
        ["Implement API fix", "Add regression test"],
    )

    assert agent == "SWE"
    assert "Default agent" in reason


def test_select_copilot_agent_uses_specialist_for_jira_requirement():
    agent, reason = _select_copilot_agent(
        {
            "summary": "Harden GitHub Actions workflow",
            "description": "Pin action versions and enforce least privilege permissions",
            "type": "Story",
            "labels": ["ci", "security"],
        },
        [],
    )

    assert agent == "GitHub Actions Expert"
    assert "keyword" in reason


def test_count_commits_ahead_parses_integer(monkeypatch):
    monkeypatch.setattr("app.orchestration._run", lambda *args, **kwargs: "3")

    count = _count_commits_ahead(repo_path="/tmp", env={}, base_branch="development")

    assert count == 3


def test_run_orchestration_rejects_empty_branch_before_creating_pr(monkeypatch, tmp_path):
    repo_base = tmp_path / "repos"
    monkeypatch.setattr("app.orchestration._REPO_BASE_DIR", repo_base)
    monkeypatch.setattr(
        "app.orchestration._prepare_env",
        lambda: {"GITHUB_TOKEN": "github-token", "COPILOT_GITHUB_TOKEN": "copilot-token"},
    )
    monkeypatch.setattr(
        "app.orchestration._normalize_repo",
        lambda repository: ("https://github.com/owner/repo.git", "owner/repo"),
    )
    monkeypatch.setattr("app.orchestration._build_branch_name", lambda jira_ticket_id: "feature/empty-branch")
    monkeypatch.setattr(
        "app.orchestration.jira_service.get_issue",
        lambda ticket_id: {"summary": "Fix empty branch flow", "description": "", "type": "Story"},
    )
    monkeypatch.setattr("app.orchestration._run_copilot_prompt", lambda *args, **kwargs: "copilot output")
    monkeypatch.setattr("app.orchestration.requests.post", lambda *args, **kwargs: pytest.fail("PR API should not be called"))

    def fake_run(cmd, cwd, env, cancellation_token=None):
        if "clone" in cmd:
            Path(cwd).mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            return ""
        if cmd[:2] == ["git", "diff"] and "--cached" in cmd:
            return ""
        if cmd[:2] == ["git", "rev-list"]:
            return "0"
        return ""

    monkeypatch.setattr("app.orchestration._run", fake_run)

    with pytest.raises(OrchestrationError, match="No commits were created on feature/empty-branch"):
        run_orchestration(
            jira_ticket_id="AGENT_FLOW-999",
            repository="owner/repo",
            base_branch="development",
            reviewer=None,
            commit_message="feat(agent_flow-999): automated implementation",
            change_plan=["Implement", "Test"],
        )


def test_extract_copilot_session_id_from_resume_text():
    output = """
      ╭─╮╭─╮   Changes    +0 -12
      ╰─╯╰─╯   AI Credits 8.17 (13s)
      █ ▘▝ █   Tokens     ↑ 21.3k (21.3k written) • ↓ 112 (27 reasoning)
       ▔▔▔▔    Resume     copilot --resume=e4380250-504d-4eee-b990-836b2998fddb
    """

    assert _extract_copilot_session_id(output) == "e4380250-504d-4eee-b990-836b2998fddb"


def test_extract_pr_review_findings_parses_structured_payload():
    output = """
    Review output
    FINDINGS_JSON_START
    [
      {
        "path": "backend/app/orchestration.py",
        "line": 120,
        "severity": "major",
        "title": "Missing guard",
        "details": "Input is not validated.",
        "suggestion": "Add explicit validation"
      }
    ]
    FINDINGS_JSON_END
    """

    findings = _extract_pr_review_findings(output)

    assert len(findings) == 1
    assert findings[0]["path"] == "backend/app/orchestration.py"
    assert findings[0]["line"] == 120
    assert findings[0]["severity"] == "MAJOR"
    assert "Missing guard" in findings[0]["body"]


def test_run_orchestration_pr_review_flow_posts_comments_and_artifacts(monkeypatch, tmp_path):
    repo_base = tmp_path / "repos"
    monkeypatch.setattr("app.orchestration._REPO_BASE_DIR", repo_base)
    monkeypatch.setattr(
        "app.orchestration._prepare_env",
        lambda: {"GITHUB_TOKEN": "github-token", "COPILOT_GITHUB_TOKEN": "copilot-token"},
    )
    monkeypatch.setattr(
        "app.orchestration._normalize_repo",
        lambda repository: ("https://github.com/owner/repo.git", "owner/repo"),
    )

    review_output = """
    ## Findings
    FINDINGS_JSON_START
    [
      {
        "path": "backend/app/main.py",
        "line": 25,
        "severity": "CRITICAL",
        "title": "Security issue",
        "details": "Token is logged",
        "suggestion": "Remove sensitive logging"
      }
    ]
    FINDINGS_JSON_END
    """
    monkeypatch.setattr("app.orchestration._run_copilot_prompt", lambda *args, **kwargs: review_output)

    posted_comments = []

    def fake_run(cmd, cwd, env, cancellation_token=None):
        if "clone" in cmd:
            Path(cwd).mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            return ""
        if cmd[:3] == ["gh", "pr", "checkout"]:
            review_dir = Path(cwd) / ".github" / "pr_review"
            review_dir.mkdir(parents=True, exist_ok=True)
            (review_dir / "feature-pr-42 Code Review.md").write_text("# Findings\n- test", encoding="utf-8")
            return ""
        return ""

    def fake_get(url, headers, timeout):
        assert url.endswith("/repos/owner/repo/pulls/42")
        return SimpleNamespace(status_code=200, json=lambda: {"head": {"sha": "abc123"}}, text="")

    def fake_post(url, headers, json, timeout):
        posted_comments.append({"url": url, "body": json["body"], "path": json["path"], "line": json["line"]})
        return SimpleNamespace(status_code=201, text="")

    monkeypatch.setattr("app.orchestration._run", fake_run)
    monkeypatch.setattr("app.orchestration.requests.get", fake_get)
    monkeypatch.setattr("app.orchestration.requests.post", fake_post)

    result = run_orchestration(
        jira_ticket_id="PR-42",
        repository="owner/repo",
        base_branch="main",
        reviewer=None,
        selected_agent="PR-Review",
        selected_model=None,
        commit_message="chore: review",
        change_plan=["Review PR #42"],
        jira_context={"summary": "Review PR #42", "description": "https://github.com/owner/repo/pull/42"},
    )

    assert result["selected_agent"] == "PR-Review"
    assert result["pull_request_url"] == "https://github.com/owner/repo/pull/42"
    assert any(step["name"] == "checkout_pull_request" for step in result["steps"])
    assert any(step["name"] == "publish_review_comments" for step in result["steps"])
    assert len(result["artifacts"]) == 3
    assert result["artifacts"][0]["path"] == ".github/pr_review/feature-pr-42 Code Review.md"
    assert any(artifact["path"] == ".github/pr_review/feature-pr-42 Code Review.md" for artifact in result["artifacts"])
    assert result["review_comment_summary"]["posted"] == 1
    assert posted_comments[0]["path"] == "backend/app/main.py"
    assert posted_comments[0]["line"] == 25


def test_run_orchestration_pr_review_can_skip_publish_comments(monkeypatch, tmp_path):
    repo_base = tmp_path / "repos"
    monkeypatch.setattr("app.orchestration._REPO_BASE_DIR", repo_base)
    monkeypatch.setattr(
        "app.orchestration._prepare_env",
        lambda: {"GITHUB_TOKEN": "github-token", "COPILOT_GITHUB_TOKEN": "copilot-token"},
    )
    monkeypatch.setattr("app.orchestration._collect_change_stats", lambda *args, **kwargs: {"added": 0, "removed": 0})
    monkeypatch.setattr("app.orchestration._build_usage_from_session_logs", lambda *args, **kwargs: {"changes": {"added": 0, "removed": 0}})

    review_output = """
    FINDINGS_JSON_START
    [
      {
        "path": "backend/app/main.py",
        "line": 25,
        "severity": "MAJOR",
        "title": "Issue",
        "details": "details",
        "suggestion": "fix"
      }
    ]
    FINDINGS_JSON_END
    """
    monkeypatch.setattr("app.orchestration._run_copilot_prompt", lambda *args, **kwargs: review_output)

    def fake_run(cmd, cwd, env, cancellation_token=None):
        if "clone" in cmd:
            Path(cwd).mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            return ""
        return ""

    monkeypatch.setattr("app.orchestration._run", fake_run)
    monkeypatch.setattr(
        "app.orchestration._publish_pr_inline_comments",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("publish should be skipped")),
    )

    result = run_orchestration(
        jira_ticket_id="PR-42",
        repository="owner/repo",
        base_branch="main",
        reviewer=None,
        selected_agent="PR-Review",
        selected_model=None,
        commit_message="chore: review",
        change_plan=["Review PR #42"],
        execution_steps=[],
        jira_context={"summary": "Review PR #42", "description": "https://github.com/owner/repo/pull/42"},
    )

    step_status = {step["name"]: step["status"] for step in result["steps"]}
    assert step_status["publish_review_comments"] == "skipped"
    assert result["review_comment_summary"]["skipped"] is True


def test_build_usage_from_session_logs_uses_shutdown_event(monkeypatch, tmp_path):
    session_id = "ca2f0a7b-69ab-4945-a4bc-45dd4aaa26d7"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True)
    events_file = session_dir / "events.jsonl"
    events_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "session.start", "data": {"noop": True}}),
                json.dumps(
                    {
                        "type": "session.shutdown",
                        "data": {
                            "totalNanoAiu": 45257190000,
                            "tokenDetails": {
                                "input": {"tokenCount": 3864},
                                "cache_read": {"tokenCount": 600658},
                                "cache_write": {"tokenCount": 36982},
                                "output": {"tokenCount": 8140},
                            },
                            "totalApiDurationMs": 162576,
                            "codeChanges": {"linesAdded": 101, "linesRemoved": 6},
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.orchestration._COPILOT_SESSION_STATE_DIR", tmp_path)

    usage = _build_usage_from_session_logs([session_id], changes_override={"added": 0, "removed": 0})

    assert usage["source"] == "copilot_session_logs"
    assert usage["session_log_found"] is True
    assert usage["session_ids"] == [session_id]
    assert usage["total_nano_aiu"] == 45257190000
    assert usage["ai_credits_used"] == 45.2572
    assert usage["ai"]["duration_seconds"] == 163
    assert usage["ai"]["total_api_duration_ms"] == 162576
    assert usage["tokens"]["input"] == 3864
    assert usage["tokens"]["output"] == 8140
    assert usage["tokens"]["cached"] == 637640
    assert usage["tokens"]["total"] == 649644
    assert usage["changes"] == {"added": 101, "removed": 6}


    def test_build_usage_from_session_logs_tolerates_missing_events_file(monkeypatch):
        monkeypatch.setattr("app.orchestration._load_shutdown_events", lambda session_ids: (_ for _ in ()).throw(FileNotFoundError("missing")))

        usage = _build_usage_from_session_logs(["session-abc"], changes_override={"added": 1, "removed": 0})

        assert usage["session_log_found"] is False
        assert usage["changes"] == {"added": 1, "removed": 0}
        assert usage["ai_credits_used"] == 0.0


def test_build_usage_from_session_logs_prefers_git_changes_override(monkeypatch, tmp_path):
    session_id = "f1111111-1111-4111-8111-111111111111"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "session.shutdown",
                "data": {
                    "totalNanoAiu": 1000000000,
                    "tokenDetails": {
                        "input": {"tokenCount": 100},
                        "cache_read": {"tokenCount": 50},
                        "cache_write": {"tokenCount": 25},
                        "output": {"tokenCount": 10},
                    },
                    "totalApiDurationMs": 1000,
                    "codeChanges": {"linesAdded": 7, "linesRemoved": 3},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.orchestration._COPILOT_SESSION_STATE_DIR", tmp_path)

    usage = _build_usage_from_session_logs([session_id], changes_override={"added": 2, "removed": 1})

    assert usage["changes"] == {"added": 2, "removed": 1}
    assert usage["session_log_found"] is True


def test_run_orchestration_retry_idempotency_guards_skip_duplicate_side_effects(monkeypatch, tmp_path):
    repo_base = tmp_path / "repos"
    monkeypatch.setattr("app.orchestration._REPO_BASE_DIR", repo_base)
    monkeypatch.setattr(
        "app.orchestration._prepare_env",
        lambda: {"GITHUB_TOKEN": "github-token", "COPILOT_GITHUB_TOKEN": "copilot-token"},
    )
    monkeypatch.setattr(
        "app.orchestration._normalize_repo",
        lambda repository: ("https://github.com/owner/repo.git", "owner/repo"),
    )
    monkeypatch.setattr("app.orchestration._build_branch_name", lambda jira_ticket_id: "feature/retry-guard")
    monkeypatch.setattr(
        "app.orchestration.jira_service.get_issue",
        lambda ticket_id: {"summary": "Retry test", "description": "", "type": "Story"},
    )
    monkeypatch.setattr("app.orchestration._run_copilot_prompt", lambda *args, **kwargs: "copilot output")
    monkeypatch.setattr("app.orchestration._count_commits_ahead", lambda *args, **kwargs: 1)
    monkeypatch.setattr("app.orchestration._collect_change_stats", lambda *args, **kwargs: {"added": 1, "removed": 0})
    monkeypatch.setattr("app.orchestration._build_usage_from_session_logs", lambda *args, **kwargs: {"ai_credits_used": 0.0})
    monkeypatch.setattr("app.orchestration.requests.post", lambda *args, **kwargs: pytest.fail("PR API should not be called"))

    executed_commands = []

    def fake_run(cmd, cwd, env, cancellation_token=None):
        executed_commands.append(cmd)
        if "clone" in cmd:
            Path(cwd).mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        if cmd[:2] == ["git", "diff"] and "--cached" in cmd:
            return ""
        return ""

    monkeypatch.setattr("app.orchestration._run", fake_run)

    result = run_orchestration(
        jira_ticket_id="AGENT_FLOW-999",
        repository="owner/repo",
        base_branch="development",
        reviewer=None,
        commit_message="feat(agent_flow-999): automated implementation",
        change_plan=["Implement", "Test"],
        retry_context={
            "retry_mode": "from_failed_step",
            "start_step": "push_branch",
            "side_effect_guards": {
                "commit_success": True,
                "push_success": True,
                "pr_success": True,
                "existing_pr_url": "https://github.com/owner/repo/pull/123",
            },
        },
    )

    assert result["pull_request_url"] == "https://github.com/owner/repo/pull/123"
    step_status = {step["name"]: step["status"] for step in result["steps"]}
    assert step_status["commit_changes"] == "skipped"
    assert step_status["push_branch"] == "skipped"
    assert step_status["create_pr"] == "skipped"

    assert not any(cmd[:2] == ["git", "commit"] for cmd in executed_commands)
    assert not any(cmd[:2] == ["git", "push"] for cmd in executed_commands)