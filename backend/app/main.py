"""FastAPI application entry point."""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse


def _load_environment() -> None:
    env_candidates = [
        Path("/app/.env"),
        Path(__file__).resolve().parent.parent.parent / ".env",
    ]
    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(env_path)
            return


_load_environment()

from app.routers import auth, chat, jira, models, orchestrate  # noqa: E402 — must import after dotenv
from app.routers.auth import SESSION_COOKIE_NAME, resolve_user_from_session  # noqa: E402

app = FastAPI(title="AGENT_FLOW Orchestrator API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _auth_disabled() -> bool:
    return os.environ.get("AGENT_FLOW_DISABLE_AUTH", "0").strip().lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def require_session_for_api(request, call_next):
    path = request.url.path

    if _auth_disabled() or path == "/health" or not path.startswith("/api") or path.startswith("/api/auth"):
        return await call_next(request)

    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    user = resolve_user_from_session(session_token)
    if not user:
        response = JSONResponse(status_code=401, content={"detail": "Authentication required"})
        response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
        return response

    request.state.user = user
    return await call_next(request)

app.include_router(auth.router)
app.include_router(jira.router)
app.include_router(models.router)
app.include_router(orchestrate.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}
