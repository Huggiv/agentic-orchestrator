"""Field filter loading and application.

Filter config (config/filters.yaml):

    default:
      fields: [list of dotted paths]
      rename: {dotted-path: output_key}
    projects:
      <KEY>:
        fields: [...]   # replaces default.fields
        rename: {...}   # merged onto default.rename (project wins)

Dotted paths: "status.name" -> raw_fields["status"]["name"].
Output key = rename[path] if set, else last segment of path.
"""

from pathlib import Path
from typing import Any

import yaml


def load_filters(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Filter config not found at {path}")
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    data.setdefault("default", {})
    data["default"].setdefault("fields", [])
    data["default"].setdefault("rename", {})
    data.setdefault("projects", {})
    return data


def resolve_filter(filters: dict, project_key: str) -> tuple[list[str], dict[str, str]]:
    """Return (fields, rename_map) for the given project key."""
    default = filters.get("default", {})
    project = filters.get("projects", {}).get(project_key, {})
    fields = project.get("fields", default.get("fields", []))
    rename = {**default.get("rename", {}), **project.get("rename", {})}
    return fields, rename


def _resolve_path(raw: dict, path: str) -> tuple[bool, Any]:
    cur: Any = raw
    for segment in path.split("."):
        if not isinstance(cur, dict) or segment not in cur or cur[segment] is None:
            return False, None
        cur = cur[segment]
    return True, cur


def apply_filter(raw_fields: dict, fields: list[str], rename: dict[str, str]) -> dict:
    out: dict = {}
    for path in fields:
        found, value = _resolve_path(raw_fields, path)
        if not found:
            continue
        out_key = rename.get(path, path.split(".")[-1])
        out[out_key] = value
    return out
