"""FastAPI application entry point."""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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

from app.routers import jira, models, orchestrate  # noqa: E402 — must import after dotenv

app = FastAPI(title="AGENT_FLOW Orchestrator API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jira.router)
app.include_router(models.router)
app.include_router(orchestrate.router)


@app.get("/health")
def health():
    return {"status": "ok"}
