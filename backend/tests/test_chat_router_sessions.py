from fastapi.testclient import TestClient

from app.history_store import get_history_store
from app.main import app


def test_session_chat_follow_up_stays_in_grooming_flow(monkeypatch):
    def fake_get_issue(ticket_id):
        return {
            "key": ticket_id,
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/sessions",
            json={"title": "Interactive session", "mode": "interactive", "client_context": {"active_repository": "owner/repo"}},
        )
        created.raise_for_status()
        session_id = created.json()["session_id"]

        first = client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "message": "Please review AGENT_FLOW-101 and help me groom the work.",
                "mode": "interactive",
                "client_context": {"active_repository": "owner/repo"},
            },
        )
        first.raise_for_status()
        first_payload = first.json()
        assert first_payload["assistant_message"]["kind"] == "grooming_review"
        assert first_payload["trigger_state"]["status"] == "grooming"

        follow_up = client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "message": "Problem: users cannot assign the workflow from chat\nUser impact: release managers are blocked\nGoals:\n- enable confirmation in chat\nConstraints:\n- keep session state\nAcceptance criteria:\n- show confirm action inline",
                "mode": "interactive",
                "client_context": {"active_repository": "owner/repo"},
            },
        )
        follow_up.raise_for_status()
        follow_payload = follow_up.json()
        assert follow_payload["assistant_message"]["kind"] == "grooming_review"
        assert follow_payload["trigger_state"]["status"] == "ready_to_trigger"


def test_prepare_trigger_returns_confirmation_message_kind(monkeypatch):
    def fake_get_issue(ticket_id):
        return {
            "key": ticket_id,
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/sessions",
            json={"title": "Prepare session", "mode": "interactive", "client_context": {"active_repository": "owner/repo"}},
        )
        created.raise_for_status()
        session_id = created.json()["session_id"]

        client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "message": "Problem: users cannot assign the workflow from chat\nUser impact: release managers are blocked\nGoals:\n- enable confirmation in chat\nConstraints:\n- keep session state\nAcceptance criteria:\n- show confirm action inline for AGENT_FLOW-101",
                "mode": "grooming",
                "client_context": {"active_repository": "owner/repo"},
            },
        ).raise_for_status()

        prepared = client.post(
            f"/api/chat/sessions/{session_id}/prepare-trigger",
            json={"repository": "owner/repo", "base_branch": "development", "selected_agent": "SWE"},
        )
        prepared.raise_for_status()
        payload = prepared.json()
        assert payload["assistant_message"]["kind"] == "session_confirmation"
        assert payload["trigger_state"]["status"] == "awaiting_confirmation"


def test_delete_session_removes_session_and_messages(monkeypatch):
    def fake_get_issue(ticket_id):
        return {
            "key": ticket_id,
            "summary": f"Summary for {ticket_id}",
            "type": "Story",
        }

    monkeypatch.setattr("app.routers.chat.jira_service.get_issue", fake_get_issue)

    with TestClient(app) as client:
        created = client.post(
            "/api/chat/sessions",
            json={"title": "Delete session", "mode": "interactive", "client_context": {"active_repository": "owner/repo"}},
        )
        created.raise_for_status()
        session_id = created.json()["session_id"]

        client.post(
            f"/api/chat/sessions/{session_id}/messages",
            json={
                "message": "Please review AGENT_FLOW-101 and help me groom the work.",
                "mode": "interactive",
                "client_context": {"active_repository": "owner/repo"},
            },
        ).raise_for_status()

        removed = client.delete(f"/api/chat/sessions/{session_id}")
        removed.raise_for_status()
        assert removed.json()["status"] == "deleted"

    assert get_history_store().get_chat_session(session_id) is None
    assert get_history_store().list_chat_messages(session_id) == []