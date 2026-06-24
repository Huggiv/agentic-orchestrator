from types import SimpleNamespace

import pytest

from app.orchestration import (
    COPILOT_AUTH_ERROR,
    OrchestrationError,
    _merge_usage,
    _parse_copilot_usage,
    _prepare_env,
    _run_copilot_prompt,
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
    def fake_run(cmd, cwd, env):
        raise OrchestrationError(
            "Authentication failed. Your GitHub token may be invalid or missing Copilot Requests permission."
        )

    monkeypatch.setattr("app.orchestration._run", fake_run)

    with pytest.raises(OrchestrationError, match="Copilot CLI authentication failed") as exc_info:
        _run_copilot_prompt("hello", cwd="/tmp", env={"COPILOT_GITHUB_TOKEN": "token"})

    assert str(exc_info.value) == COPILOT_AUTH_ERROR


def test_parse_copilot_usage_handles_decorated_summary_lines():
    output = """
      ╭─╮╭─╮   Changes    +0 -12
      ╰─╯╰─╯   AI Credits 8.17 (13s)
      █ ▘▝ █   Tokens     ↑ 21.3k (21.3k written) • ↓ 112 (27 reasoning)
       ▔▔▔▔    Resume     copilot --resume=e4380250-504d-4eee-b990-836b2998fddb
    """

    parsed = _parse_copilot_usage(output)

    assert parsed["changes_added"] == 0
    assert parsed["changes_removed"] == 12
    assert parsed["ai_credits_used"] == 8.17
    assert parsed["ai_elapsed_seconds"] == 13
    assert parsed["tokens_input_total"] == 21_300
    assert parsed["tokens_input_written"] == 21_300
    assert parsed["tokens_output_total"] == 112
    assert parsed["tokens_output_reasoning"] == 27


def test_merge_usage_prefers_parsed_changes_when_git_diff_is_empty():
    merged = _merge_usage(
        estimated_usage={"ai_credits_used": 1, "estimated_cost_usd": 0.01},
        snapshots=[
            {
                "changes_added": 5,
                "changes_removed": 3,
                "ai_credits_used": 1.5,
                "ai_elapsed_seconds": 12,
            }
        ],
        changes_override={"added": 0, "removed": 0},
    )

    assert merged["changes"] == {"added": 5, "removed": 3}
    assert merged["ai_credits_used"] == 1.5
    assert merged["estimated_cost_usd"] == 0.015