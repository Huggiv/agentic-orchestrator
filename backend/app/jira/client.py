"""Jira client – reads JIRA_URL and JIRA_PAT from environment."""

import os

from jira import JIRA
from jira.exceptions import JIRAError


class JiraConfigError(Exception):
    pass


class JiraAuthError(Exception):
    pass


def get_client() -> JIRA:
    url = os.environ.get("JIRA_URL") or os.environ.get("JIRA_BASE_URL")
    pat = os.environ.get("JIRA_PAT") or os.environ.get("JIRA_TOKEN")
    missing = [k for k, v in (("JIRA_URL", url), ("JIRA_PAT", pat)) if not v]
    if missing:
        raise JiraConfigError(
            f"Missing required env vars: {', '.join(missing)}"
        )
    verify_ssl = os.environ.get("JIRA_VERIFY_SSL", "true").lower() != "false"
    try:
        return JIRA(server=url, token_auth=pat, options={"verify": verify_ssl})
    except JIRAError as e:
        if e.status_code == 401:
            raise JiraAuthError("Jira rejected the PAT (401). Refresh JIRA_PAT.") from e
        raise


def get_base_url() -> str:
    return (os.environ.get("JIRA_URL") or os.environ.get("JIRA_BASE_URL") or "").rstrip("/")
