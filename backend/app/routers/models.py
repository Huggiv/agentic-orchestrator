"""Backend router for listing available Copilot models."""

from __future__ import annotations

import re
import subprocess

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["models"])

# Module-level cache so the copilot CLI subprocess is only invoked once per
# backend process lifetime.
_cached_models: list[dict] | None = None

_ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
_MODEL_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*`?([^`|]+?)`?\s*\|")


def _parse_model_table(output: str) -> list[dict]:
    """Parse a Markdown table row into {'name': ..., 'id': ...} entries.

    Expected row format:
        | Claude Sonnet 4.6 | `claude-sonnet-4.6` |
    """
    models: list[dict] = []
    seen_ids: set[str] = set()

    normalized_output = _ANSI_ESCAPE_RE.sub("", output)
    for line in normalized_output.splitlines():
        # Match rows with a human label and model identifier. Newer CLI output
        # may omit backticks around IDs, so both forms are supported.
        m = _MODEL_ROW_RE.match(line.strip())
        if m is None:
            continue
        name = m.group(1).strip()
        model_id = m.group(2).strip()
        # Skip header rows (e.g. "Model | ID")
        if not name or not model_id or name.lower() in ("model", "-", "---"):
            continue
        if model_id.lower() in ("id", "-", "---"):
            continue
        if model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        models.append({"name": name, "id": model_id})
    return models


def _load_models() -> list[dict]:
    global _cached_models
    if _cached_models is not None:
        return _cached_models

    try:
        result = subprocess.run(
            ["copilot", "-p", "/models"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout or ""
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="copilot CLI not found in PATH")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="copilot CLI timed out listing models")

    models = _parse_model_table(output)
    if not models and result.returncode != 0:
        stderr = (result.stderr or "").strip()
        detail = "copilot CLI failed to list models"
        if stderr:
            detail = f"{detail}: {stderr}"
        raise HTTPException(status_code=503, detail=detail)

    _cached_models = models
    return _cached_models


@router.get("/models")
def list_models():
    """Return available Copilot models parsed from `copilot -p /models`."""
    models = _load_models()
    return {"models": models}
