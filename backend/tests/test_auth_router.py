from datetime import timedelta

from fastapi.testclient import TestClient

from app.auth_store import reset_auth_store_for_tests
from app.history_store import reset_history_store_for_tests
from app.main import app


def _enable_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_FLOW_AUTH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("AGENT_FLOW_HISTORY_DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("AGENT_FLOW_DISABLE_AUTH", "0")
    reset_auth_store_for_tests()
    reset_history_store_for_tests()


def _signup(client, *, name, email, password, confirm=None):
    return client.post(
        "/api/auth/signup",
        json={
            "name": name,
            "email": email,
            "password": password,
            "confirm_password": confirm if confirm is not None else password,
        },
    )


STRONG = "Str0ng!Pass"


def test_signup_rejects_weak_password_and_mismatch(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path)

    with TestClient(app) as client:
        weak = _signup(client, name="Weak", email="weak@example.com", password="weakpass")
        assert weak.status_code == 400
        assert "at least one uppercase" in weak.json()["detail"].lower() or "number" in weak.json()["detail"].lower()

        mismatch = _signup(
            client,
            name="Mismatch",
            email="mismatch@example.com",
            password=STRONG,
            confirm="Different1!",
        )
        assert mismatch.status_code == 400
        assert "match" in mismatch.json()["detail"].lower()

    reset_auth_store_for_tests()
    reset_history_store_for_tests()


def test_first_user_is_admin_and_login_creates_session(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path)

    with TestClient(app) as client:
        signup_response = _signup(client, name="Owner", email="owner@example.com", password=STRONG)
        signup_response.raise_for_status()
        body = signup_response.json()
        assert body["authenticated"] is True
        assert body["user"]["role"] == "admin"

        # Authenticated session works against a protected read endpoint.
        agents = client.get("/api/agents")
        agents.raise_for_status()

    # Fresh client without cookies must log in.
    with TestClient(app) as fresh:
        bad = fresh.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong"})
        assert bad.status_code == 401

        ok = fresh.post("/api/auth/login", json={"email": "owner@example.com", "password": STRONG})
        ok.raise_for_status()
        assert ok.json()["user"]["role"] == "admin"

    reset_auth_store_for_tests()
    reset_history_store_for_tests()


def test_role_based_permissions(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path)

    def fake_run_orchestration(**kwargs):
        return {"branch_name": "feature/x", "pull_request_url": "https://example/pr/1", "steps": []}

    monkeypatch.setattr("app.routers.orchestrate.run_orchestration", lambda *a, **k: fake_run_orchestration())

    admin = TestClient(app)
    developer = TestClient(app)
    user = TestClient(app)

    try:
        # First signup -> admin.
        _signup(admin, name="Admin", email="admin@example.com", password=STRONG).raise_for_status()
        # Second + third signups default to 'user'.
        _signup(developer, name="Dev", email="dev@example.com", password=STRONG).raise_for_status()
        _signup(user, name="User", email="user@example.com", password=STRONG).raise_for_status()

        # Admin promotes the second account to developer.
        promote = admin.post(
            "/api/auth/users/role",
            json={"email": "dev@example.com", "role": "developer"},
        )
        promote.raise_for_status()
        assert promote.json()["user"]["role"] == "developer"
        # Refresh developer session view of role.
        developer.get("/api/auth/session").raise_for_status()

        run_body = {
            "jira_ticket_id": "DEMO-1",
            "repository": "owner/repo",
            "base_branch": "development",
            "selected_agent": "SWE",
            "commit_message": "feat(demo-1): automated",
            "change_plan": ["Implement"],
        }

        # Run permission: user denied, developer + admin allowed.
        assert user.post("/api/orchestrate", json=run_body).status_code == 403
        assert developer.post("/api/orchestrate", json=run_body).status_code == 200
        assert admin.post("/api/orchestrate", json=run_body).status_code == 200

        # Delete: only admin (non-existent id -> 404 means permission passed).
        assert user.delete("/api/orchestrate/none").status_code == 403
        assert developer.delete("/api/orchestrate/none").status_code == 403
        assert admin.delete("/api/orchestrate/none").status_code == 404

        # Purge: only admin.
        assert user.post("/api/orchestrate/history/purge?days=30").status_code == 403
        assert developer.post("/api/orchestrate/history/purge?days=30").status_code == 403
        assert admin.post("/api/orchestrate/history/purge?days=30").status_code == 200

        # History read is allowed for everyone authenticated, including plain users.
        assert user.get("/api/orchestrate/history").status_code == 200

        # Admin-only user management is denied for non-admins.
        assert user.get("/api/auth/users").status_code == 403
        assert admin.get("/api/auth/users").status_code == 200
    finally:
        admin.close()
        developer.close()
        user.close()
        reset_auth_store_for_tests()
        reset_history_store_for_tests()


def test_session_expires_after_inactivity_window(monkeypatch, tmp_path):
    _enable_auth(monkeypatch, tmp_path)

    with TestClient(app) as client:
        _signup(client, name="Timeout", email="timeout@example.com", password=STRONG).raise_for_status()

        from app.routers import auth as auth_router

        baseline = auth_router._utcnow()
        monkeypatch.setattr(auth_router, "_utcnow", lambda: baseline + timedelta(days=2))

        session_response = client.get("/api/auth/session")
        session_response.raise_for_status()
        assert session_response.json() == {"authenticated": False}

        protected = client.get("/api/agents")
        assert protected.status_code == 401

    reset_auth_store_for_tests()
    reset_history_store_for_tests()
