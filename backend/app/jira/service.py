"""Jira service: fetch and format issues."""

from pathlib import Path
from typing import Optional

from jira.exceptions import JIRAError

from .client import get_client, get_base_url, JiraConfigError, JiraAuthError
from .filters import load_filters, resolve_filter, apply_filter

_FILTERS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "filters.yaml"


def _load_filters() -> dict:
    return load_filters(_FILTERS_PATH)


def _project_key(issue_key: str) -> str:
    return issue_key.split("-", 1)[0] if "-" in issue_key else issue_key


def _format_issue(raw_issue: dict, filters_cfg: dict) -> dict:
    """Apply field filter to a raw Jira issue dict and inject key + url."""
    issue_key = raw_issue["key"]
    project = _project_key(issue_key)
    fields, rename = resolve_filter(filters_cfg, project)
    out: dict = {
        "key": issue_key,
        "url": f"{get_base_url()}/browse/{issue_key}",
    }
    out.update(apply_filter(raw_issue.get("fields", {}), fields, rename))
    return out


def get_issues(jql: Optional[str] = None, max_results: int = 25) -> list[dict]:
    """Fetch Jira issues matching *jql* (defaults to global recent updates)."""
    client = get_client()
    effective_jql = jql or 'ORDER BY updated DESC'
    issues = client.search_issues(effective_jql, maxResults=max_results)
    filters_cfg = _load_filters()
    return [_format_issue(i.raw, filters_cfg) for i in issues]


def get_issue(issue_key: str) -> dict:
    """Fetch a single Jira issue by key."""
    client = get_client()
    issue = client.issue(issue_key)
    filters_cfg = _load_filters()
    return _format_issue(issue.raw, filters_cfg)
