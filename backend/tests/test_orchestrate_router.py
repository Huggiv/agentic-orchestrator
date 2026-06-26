import time

from fastapi.testclient import TestClient

from app.history_store import reset_history_store_for_tests
from app.main import app


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
    ):
        assert selected_agent == "SWE"
        assert selected_model is None
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
    queued_payloads = []

    def fake_get_issue(ticket_id):
        return {
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    def fake_enqueue(payload, request_context=None):
        queued_payloads.append({"payload": payload, "request_context": request_context})
        return {"job_id": f"job-{payload.jira_ticket_id.lower()}", "status": "queued"}

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)
    monkeypatch.setattr("app.routers.chat.enqueue_orchestration", fake_enqueue)

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
    assert [job["jira_ticket_id"] for job in payload["queued_jobs"]] == ["AGENT_FLOW-12", "AGENT_FLOW-99"]
    assert payload["failed_tickets"] == []
    assert len(queued_payloads) == 2
    assert queued_payloads[0]["payload"].jira_ticket_id == "AGENT_FLOW-12"
    assert queued_payloads[1]["payload"].jira_ticket_id == "AGENT_FLOW-99"
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
    assert "Please include at least one Jira ticket key" in payload["assistant_message"]