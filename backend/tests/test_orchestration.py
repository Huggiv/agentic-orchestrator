import json
from types import SimpleNamespace

import pytest

from app.orchestration import (
    COPILOT_AUTH_ERROR,
    OrchestrationError,
    _build_usage_from_session_logs,
    _extract_copilot_session_id,
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


def test_extract_copilot_session_id_from_resume_text():
    output = """
      ╭─╮╭─╮   Changes    +0 -12
      ╰─╯╰─╯   AI Credits 8.17 (13s)
      █ ▘▝ █   Tokens     ↑ 21.3k (21.3k written) • ↓ 112 (27 reasoning)
       ▔▔▔▔    Resume     copilot --resume=e4380250-504d-4eee-b990-836b2998fddb
    """

    assert _extract_copilot_session_id(output) == "e4380250-504d-4eee-b990-836b2998fddb"


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