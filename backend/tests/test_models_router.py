import subprocess

from fastapi.testclient import TestClient

from app.main import app
from app.routers import models as models_router


def test_parse_model_table_accepts_backticks_and_plain_ids():
    raw = """
| Model | ID |
| --- | --- |
| Claude Sonnet 4.6 | `claude-sonnet-4.6` |
| GPT 5 Mini | gpt-5-mini |
| GPT 5 Mini | gpt-5-mini |
"""

    parsed = models_router._parse_model_table(raw)

    assert parsed == [
        {"name": "Claude Sonnet 4.6", "id": "claude-sonnet-4.6"},
        {"name": "GPT 5 Mini", "id": "gpt-5-mini"},
    ]


def test_models_route_returns_503_when_cli_fails(monkeypatch):
    models_router._cached_models = None

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout="",
            stderr="GitHub Copilot CLI requires Node.js v24 or higher.",
        )

    monkeypatch.setattr(models_router.subprocess, "run", fake_run)

    with TestClient(app) as client:
        response = client.get("/api/models")

    assert response.status_code == 503
    assert "failed to list models" in response.json()["detail"].lower()
