from typing import Optional

from app.jira.client import JiraConfigError, JiraAuthError
from app.jira import service as jira_service

from fastapi import APIRouter, HTTPException, Query
from jira.exceptions import JIRAError

router = APIRouter(prefix="/api/jira", tags=["jira"])


@router.get("/issues")
def list_issues(
    jql: Optional[str] = Query(default=None, description="JQL query string"),
    max_results: int = Query(default=25, ge=1, le=100),
):
    """Return a filtered list of Jira issues."""
    try:
        return {"issues": jira_service.get_issues(jql=jql, max_results=max_results)}
    except JiraConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except JiraAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except JIRAError as e:
        raise HTTPException(status_code=e.status_code or 500, detail=str(e))


@router.get("/issues/{issue_key}")
def get_issue(issue_key: str):
    """Return a single Jira issue by key (e.g. PROJ-123)."""
    try:
        return jira_service.get_issue(issue_key)
    except JiraConfigError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except JiraAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except JIRAError as e:
        raise HTTPException(status_code=e.status_code or 500, detail=str(e))
