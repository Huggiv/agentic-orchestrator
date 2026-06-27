import time
from threading import Event
from pathlib import Path

from fastapi.testclient import TestClient

from app.history_store import get_history_store, reset_history_store_for_tests
from app.main import app
from app.orchestration import OrchestrationCancelled


def _wait_for_status(client: TestClient, job_id: str, expected: str, timeout_seconds: float = 3.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/api/orchestrate/{job_id}")
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == expected:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach status {expected!r} within timeout")


def test_orchestrate_history_persists_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FLOW_HISTORY_DB_PATH", str(tmp_path / "orchestration-history.db"))
    reset_history_store_for_tests()

    def fake_run_orchestration(
        jira_ticket_id,
        repository,
        base_branch,
        reviewer,
        selected_agent,
        selected_model,
        commit_message,
        change_plan,
        progress_callback,
        cancellation_token,
        run_id,
    ):
        assert selected_agent == "SWE"
        assert selected_model is None
        assert cancellation_token is not None
        assert run_id
        progress_callback({"name": "prepare_branch", "status": "running", "details": base_branch, "timestamp": "2026-06-23T00:00:01+00:00"})
        progress_callback({"name": "prepare_branch", "status": "success", "details": f"feature/{jira_ticket_id.lower()}", "timestamp": "2026-06-23T00:00:03+00:00"})
        return {
            "branch_name": f"feature/{jira_ticket_id.lower()}",
            "pull_request_url": f"https://github.com/{repository}/pull/1",
            "steps": [{"name": "prepare_branch", "status": "success"}],
            "copilot_notes": ["Applied changes and tests"],
            "usage": {"ai_credits_used": 1.0, "estimated_cost_usd": 0.01},
        }

    monkeypatch.setattr("app.routers.orchestrate.run_orchestration", fake_run_orchestration)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/orchestrate",
            json={
                "jira_ticket_id": "AGENT_FLOW-222",
                "repository": "owner/repo",
                "base_branch": "development",
                "reviewer": None,
                "selected_agent": "SWE",
                "commit_message": "feat(agent_flow-222): automated implementation",
                "change_plan": ["Implement", "Test"],
            },
        )
        create_response.raise_for_status()
        job_id = create_response.json()["job_id"]

        status_payload = _wait_for_status(client, job_id=job_id, expected="success")
        assert status_payload["result"]["branch_name"] == "feature/agent_flow-222"
        assert len(status_payload["progress"]) == 2

        history_response = client.get("/api/orchestrate/history?limit=10")
        history_response.raise_for_status()
        items = history_response.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == job_id
        assert items[0]["request"]["jira_ticket_id"] == "AGENT_FLOW-222"
        assert items[0]["request"]["selected_agent"] == "SWE"
        assert items[0]["result"]["pull_request_url"] == "https://github.com/owner/repo/pull/1"

    reset_history_store_for_tests()


def test_agents_endpoint_lists_available_agents(monkeypatch):
    monkeypatch.setattr("app.routers.orchestrate._discover_agent_names", lambda: ["DevOps Expert", "SWE"])

    with TestClient(app) as client:
        response = client.get("/api/agents")
        response.raise_for_status()
        payload = response.json()

    assert payload == {"items": ["DevOps Expert", "SWE"]}


def test_chat_message_queues_multiple_ticket_workflows(monkeypatch):
    def fake_get_issue(ticket_id):
        return {
            "key": ticket_id,
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)
    monkeypatch.setattr("app.routers.chat._groom_with_llm", lambda *args, **kwargs: "## Groomed Scope\n- Example")

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/message",
            json={
                "message": "Please implement AGENT_FLOW-12 and AGENT_FLOW-99 with robust tests.",
                "repository": "owner/repo",
                "base_branch": "development",
                "selected_agent": "SWE",
                "selected_model": "gpt-5.3-codex",
            },
        )
        response.raise_for_status()
        payload = response.json()

    assert payload["tickets"] == ["AGENT_FLOW-12", "AGENT_FLOW-99"]
    assert payload["queued_jobs"] == []
    assert payload["failed_tickets"] == []
    assert payload["requires_confirmation"] is True
    assert payload["plan_id"]
    assert "groomed" in payload["assistant_message"].lower()


def test_chat_confirm_triggers_workflows_after_approval(monkeypatch):
    queued_payloads = []

    def fake_get_issue(ticket_id):
        return {
            "key": ticket_id,
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    def fake_enqueue(payload, request_context=None):
        queued_payloads.append({"payload": payload, "request_context": request_context})
        return {"job_id": f"job-{payload.jira_ticket_id.lower()}", "status": "queued"}

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)
    monkeypatch.setattr("app.routers.chat._groom_with_llm", lambda *args, **kwargs: "## Groomed Scope\n- Example")
    monkeypatch.setattr("app.routers.chat.enqueue_orchestration", fake_enqueue)

    with TestClient(app) as client:
        draft_response = client.post(
            "/api/chat/message",
            json={
                "message": "Please implement AGENT_FLOW-12 with robust tests.",
                "repository": "owner/repo",
                "base_branch": "development",
            },
        )
        draft_response.raise_for_status()
        plan_id = draft_response.json()["plan_id"]

        confirm_response = client.post(
            "/api/chat/confirm",
            json={"plan_id": plan_id, "confirm": True},
        )
        confirm_response.raise_for_status()
        payload = confirm_response.json()

    assert payload["confirmed"] is True
    assert [job["jira_ticket_id"] for job in payload["queued_jobs"]] == ["AGENT_FLOW-12"]
    assert len(queued_payloads) == 1
    assert queued_payloads[0]["payload"].jira_ticket_id == "AGENT_FLOW-12"
    assert queued_payloads[0]["request_context"]["trigger_source"] == "chat"


def test_chat_message_without_ticket_returns_guidance():
    with TestClient(app) as client:
        response = client.post(
            "/api/chat/message",
            json={
                "message": "Can you help me plan an implementation?",
                "repository": "owner/repo",
            },
        )
        response.raise_for_status()
        payload = response.json()

    assert payload["tickets"] == []
    assert payload["queued_jobs"] == []
    assert payload["failed_tickets"] == []
    assert payload["assistant_message"]


def test_chat_message_without_ticket_uses_llm_reply(monkeypatch):
    monkeypatch.setattr("app.routers.chat._respond_with_llm", lambda prompt, selected_model: "- short answer")

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/message",
            json={
                "message": "Can you help me plan an implementation?",
                "repository": "owner/repo",
            },
        )
        response.raise_for_status()
        payload = response.json()

    assert payload["assistant_message"] == "- short answer"


def test_chat_stream_emits_result_and_done_events(monkeypatch):
    def fake_get_issue(ticket_id):
        return {
            "key": ticket_id,
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)
    monkeypatch.setattr("app.routers.chat._groom_with_llm", lambda *args, **kwargs: "## Groomed Scope\n- Example")

    with TestClient(app) as client:
        with client.stream(
            "POST",
            "/api/chat/message/stream",
            json={
                "message": "Run AGENT_FLOW-44 with tests.",
                "repository": "owner/repo",
            },
        ) as response:
            response.raise_for_status()
            body = "".join(response.iter_text())

    assert "event: assistant_token" in body
    assert "event: result" in body
    assert "event: done" in body
    assert "plan-" in body


def test_chat_cancel_job_rejects_non_chat_trigger(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FLOW_HISTORY_DB_PATH", str(tmp_path / "orchestration-history.db"))
    reset_history_store_for_tests()


def test_success_status_preserved_when_full_result_persistence_fails_once(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FLOW_HISTORY_DB_PATH", str(tmp_path / "orchestration-history.db"))
    reset_history_store_for_tests()

    def fake_run_orchestration(
        jira_ticket_id,
        repository,
        base_branch,
        reviewer,
        selected_agent,
        selected_model,
        commit_message,
        change_plan,
        progress_callback,
        cancellation_token,
        run_id,
    ):
        progress_callback({"name": "prepare_branch", "status": "success", "details": base_branch, "timestamp": "2026-06-23T00:00:01+00:00"})
        return {
            "branch_name": f"feature/{jira_ticket_id.lower()}",
            "pull_request_url": f"https://github.com/{repository}/pull/42",
            "workspace_dir": f"/tmp/{run_id}",
            "steps": [{"name": "prepare_branch", "status": "success"}],
            "artifacts": [{"path": ".agent_flow_agentic/note.md", "content": "ok"}],
            "copilot_notes": ["large output"],
            "usage": {"ai_credits_used": 0.2, "estimated_cost_usd": 0.002},
        }

    monkeypatch.setattr("app.routers.orchestrate.run_orchestration", fake_run_orchestration)

    history_store = get_history_store()
    original_set_job_fields = history_store.set_job_fields
    state = {"raised": False}

    def flaky_set_job_fields(job_id, **fields):
        if fields.get("status") == "success" and "result" in fields and not state["raised"]:
            state["raised"] = True
            raise ValueError("serialization failed")
        return original_set_job_fields(job_id, **fields)

    monkeypatch.setattr(history_store, "set_job_fields", flaky_set_job_fields)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/orchestrate",
            json={
                "jira_ticket_id": "AGENT_FLOW-510",
                "repository": "owner/repo",
                "base_branch": "development",
                "reviewer": None,
                "selected_agent": "SWE",
                "commit_message": "feat(agent_flow-510): automated implementation",
                "change_plan": ["Implement", "Test"],
            },
        )
        create_response.raise_for_status()
        job_id = create_response.json()["job_id"]

        payload = _wait_for_status(client, job_id=job_id, expected="success", timeout_seconds=3.0)

    assert payload["status"] == "success"
    assert payload["result"]["pull_request_url"] == "https://github.com/owner/repo/pull/42"
    assert payload["result"]["artifacts"][0]["path"] == ".agent_flow_agentic/note.md"
    assert payload["result"]["warnings"]

    reset_history_store_for_tests()

    def fake_run_orchestration(
        jira_ticket_id,
        repository,
        base_branch,
        reviewer,
        selected_agent,
        selected_model,
        commit_message,
        change_plan,
        progress_callback,
        cancellation_token,
        run_id,
    ):
        return {
            "branch_name": f"feature/{jira_ticket_id.lower()}",
            "pull_request_url": f"https://github.com/{repository}/pull/1",
            "workspace_dir": f"/tmp/{run_id}",
            "steps": [{"name": "prepare_branch", "status": "success"}],
            "copilot_notes": [],
            "usage": {"ai_credits_used": 0.1, "estimated_cost_usd": 0.001},
        }

    monkeypatch.setattr("app.routers.orchestrate.run_orchestration", fake_run_orchestration)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/orchestrate",
            json={
                "jira_ticket_id": "AGENT_FLOW-500",
                "repository": "owner/repo",
                "base_branch": "development",
                "reviewer": None,
                "selected_agent": "SWE",
                "commit_message": "feat(agent_flow-500): automated implementation",
                "change_plan": ["Implement", "Test"],
            },
        )
        create_response.raise_for_status()
        job_id = create_response.json()["job_id"]
        _wait_for_status(client, job_id=job_id, expected="success")

        cancel_response = client.post(f"/api/chat/cancel/{job_id}")
        assert cancel_response.status_code == 409

    reset_history_store_for_tests()


def test_cancel_orchestration_marks_job_cancelled(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FLOW_HISTORY_DB_PATH", str(tmp_path / "orchestration-history.db"))
    reset_history_store_for_tests()

    started = Event()

    def fake_run_orchestration(
        jira_ticket_id,
        repository,
        base_branch,
        reviewer,
        selected_agent,
        selected_model,
        commit_message,
        change_plan,
        progress_callback,
        cancellation_token,
        run_id,
    ):
        started.set()
        while not cancellation_token.is_cancelled:
            time.sleep(0.02)
        raise OrchestrationCancelled("Cancelled by user request")

    monkeypatch.setattr("app.routers.orchestrate.run_orchestration", fake_run_orchestration)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/orchestrate",
            json={
                "jira_ticket_id": "AGENT_FLOW-300",
                "repository": "owner/repo",
                "base_branch": "development",
                "reviewer": None,
                "selected_agent": "SWE",
                "commit_message": "feat(agent_flow-300): automated implementation",
                "change_plan": ["Implement", "Test"],
            },
        )
        create_response.raise_for_status()
        job_id = create_response.json()["job_id"]

        deadline = time.time() + 2
        while not started.is_set() and time.time() < deadline:
            time.sleep(0.02)

        cancel_response = client.post(f"/api/orchestrate/{job_id}/cancel")
        cancel_response.raise_for_status()
        cancel_payload = cancel_response.json()
        assert cancel_payload["cancelled"] is True

        status_payload = _wait_for_status(client, job_id=job_id, expected="cancelled", timeout_seconds=3.0)
        assert status_payload["status"] == "cancelled"

    reset_history_store_for_tests()


def test_delete_orchestration_removes_history_and_workspace(monkeypatch, tmp_path):
    repo_base = tmp_path / "repos"
    repo_base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_FLOW_REPO_BASE_DIR", str(repo_base))
    monkeypatch.setenv("AGENT_FLOW_HISTORY_DB_PATH", str(tmp_path / "orchestration-history.db"))
    reset_history_store_for_tests()

    def fake_run_orchestration(
        jira_ticket_id,
        repository,
        base_branch,
        reviewer,
        selected_agent,
        selected_model,
        commit_message,
        change_plan,
        progress_callback,
        cancellation_token,
        run_id,
    ):
        workspace_dir = repo_base / run_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "repo").mkdir(parents=True, exist_ok=True)
        progress_callback({"name": "prepare_branch", "status": "success", "details": base_branch, "timestamp": "2026-06-23T00:00:01+00:00"})
        return {
            "branch_name": f"feature/{jira_ticket_id.lower()}",
            "pull_request_url": f"https://github.com/{repository}/pull/1",
            "workspace_dir": str(workspace_dir),
            "steps": [{"name": "prepare_branch", "status": "success"}],
            "copilot_notes": [],
            "usage": {"ai_credits_used": 0.1, "estimated_cost_usd": 0.001},
        }

    monkeypatch.setattr("app.routers.orchestrate.run_orchestration", fake_run_orchestration)

    with TestClient(app) as client:
        create_response = client.post(
            "/api/orchestrate",
            json={
                "jira_ticket_id": "AGENT_FLOW-404",
                "repository": "owner/repo",
                "base_branch": "development",
                "reviewer": None,
                "selected_agent": "SWE",
                "commit_message": "feat(agent_flow-404): automated implementation",
                "change_plan": ["Implement", "Test"],
            },
        )
        create_response.raise_for_status()
        job_id = create_response.json()["job_id"]

        _wait_for_status(client, job_id=job_id, expected="success", timeout_seconds=3.0)

        workspace_dir = repo_base / f"agent_flow-agentic-{job_id[:8]}"
        assert workspace_dir.exists()

        delete_response = client.delete(f"/api/orchestrate/{job_id}")
        delete_response.raise_for_status()
        assert delete_response.json()["deleted"] is True

        status_response = client.get(f"/api/orchestrate/{job_id}")
        assert status_response.status_code == 404
        assert not workspace_dir.exists()

    reset_history_store_for_tests()


def test_purge_history_endpoint_returns_deleted_count(monkeypatch):
    monkeypatch.setattr("app.routers.orchestrate.get_history_store", lambda: type("S", (), {"purge_old_jobs": lambda self, days: 4})())

    with TestClient(app) as client:
        response = client.post("/api/orchestrate/history/purge?days=30")
        response.raise_for_status()
        payload = response.json()

    assert payload == {"deleted": 4, "days": 30}