from app.history_store import HistoryStore
from datetime import datetime, timedelta, timezone


def test_history_store_persists_job_and_progress(tmp_path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=str(db_path))

    job_id = "job-123"
    request_payload = {
        "jira_ticket_id": "AGENT_FLOW-101",
        "repository": "owner/repo",
        "base_branch": "development",
    }
    store.create_job(job_id=job_id, created_at="2026-06-23T10:00:00+00:00", request_payload=request_payload)
    store.set_job_fields(job_id, status="running", started_at="2026-06-23T10:00:05+00:00")
    store.append_progress(
        job_id,
        {
            "timestamp": "2026-06-23T10:00:06+00:00",
            "name": "prepare_branch",
            "status": "running",
            "details": "development",
        },
    )
    store.append_progress(
        job_id,
        {
            "timestamp": "2026-06-23T10:00:10+00:00",
            "name": "prepare_branch",
            "status": "success",
            "details": "feature/agent_flow-101",
        },
    )

    result_payload = {
        "branch_name": "feature/agent_flow-101",
        "pull_request_url": "https://github.com/owner/repo/pull/1",
        "steps": [{"name": "prepare_branch", "status": "success"}],
    }
    store.set_job_fields(
        job_id,
        status="success",
        finished_at="2026-06-23T10:05:00+00:00",
        result=result_payload,
        error=None,
    )

    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "success"
    assert job["request"]["jira_ticket_id"] == "AGENT_FLOW-101"
    assert job["result"]["branch_name"] == "feature/agent_flow-101"
    assert len(job["progress"]) == 2
    assert job["progress"][0]["status"] == "running"
    assert job["progress"][1]["status"] == "success"

    history_items = store.list_jobs(limit=5)
    assert len(history_items) == 1
    assert history_items[0]["id"] == job_id
    assert history_items[0]["result"]["pull_request_url"].endswith("/pull/1")

    store.close()


def test_history_store_purges_jobs_older_than_30_days(tmp_path):
    db_path = tmp_path / "history.db"
    store = HistoryStore(db_path=str(db_path))

    old_created_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    fresh_created_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

    store.create_job(
        job_id="job-old",
        created_at=old_created_at,
        request_payload={"jira_ticket_id": "AGENT_FLOW-OLD", "repository": "owner/repo", "base_branch": "development"},
    )
    store.append_progress(
        "job-old",
        {
            "timestamp": old_created_at,
            "name": "prepare_branch",
            "status": "success",
            "details": "feature/agent_flow-old",
        },
    )

    store.create_job(
        job_id="job-fresh",
        created_at=fresh_created_at,
        request_payload={"jira_ticket_id": "AGENT_FLOW-NEW", "repository": "owner/repo", "base_branch": "development"},
    )

    # Retention is applied automatically on write operations.
    assert store.get_job("job-old") is None

    deleted = store.purge_old_jobs(days=30)
    assert deleted == 0

    assert store.get_job("job-fresh") is not None

    all_jobs = store.list_jobs(limit=10, include_progress=True)
    assert [job["id"] for job in all_jobs] == ["job-fresh"]

    store.close()