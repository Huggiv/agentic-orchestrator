"""SQLite-backed persistence for orchestration jobs and progress history."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class HistoryStore:
    """Persist orchestration runs, status updates, and progress events."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    @staticmethod
    def _retention_cutoff_iso(days: int = 30) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return cutoff.isoformat()

    def _purge_old_jobs_locked(self, days: int = 30) -> int:
        cutoff = self._retention_cutoff_iso(days)
        old_job_rows = self._conn.execute(
            """
            SELECT id
            FROM orchestration_jobs
            WHERE created_at < ?
            """,
            (cutoff,),
        ).fetchall()
        if not old_job_rows:
            return 0

        job_ids = [row["id"] for row in old_job_rows]
        placeholders = ",".join("?" for _ in job_ids)
        self._conn.execute(
            f"DELETE FROM orchestration_progress WHERE job_id IN ({placeholders})",
            job_ids,
        )
        self._conn.execute(
            f"DELETE FROM orchestration_jobs WHERE id IN ({placeholders})",
            job_ids,
        )
        return len(job_ids)

    def purge_old_jobs(self, days: int = 30) -> int:
        with self._lock:
            deleted = self._purge_old_jobs_locked(days)
            if deleted:
                self._conn.commit()
            return deleted

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orchestration_jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT
                );

                CREATE TABLE IF NOT EXISTS orchestration_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT,
                    FOREIGN KEY(job_id) REFERENCES orchestration_jobs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_progress_job_id
                ON orchestration_progress(job_id, id);
                """
            )
            self._purge_old_jobs_locked(days=30)
            self._conn.commit()

    @staticmethod
    def _to_json(value: Any) -> str:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def _from_json(value: str | None) -> Any:
        if not value:
            return None
        return json.loads(value)

    def create_job(self, job_id: str, created_at: str, request_payload: dict[str, Any]) -> None:
        with self._lock:
            self._purge_old_jobs_locked(days=30)
            self._conn.execute(
                """
                INSERT INTO orchestration_jobs (
                    id, status, created_at, started_at, finished_at, error, request_json, result_json
                ) VALUES (?, ?, ?, NULL, NULL, NULL, ?, NULL)
                """,
                (job_id, "queued", created_at, self._to_json(request_payload)),
            )
            self._conn.commit()

    def set_job_fields(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return

        db_fields = dict(fields)
        if "request" in db_fields:
            db_fields["request_json"] = self._to_json(db_fields.pop("request"))
        if "result" in db_fields:
            db_fields["result_json"] = self._to_json(db_fields.pop("result"))

        valid_columns = {
            "status",
            "started_at",
            "finished_at",
            "error",
            "request_json",
            "result_json",
        }
        unknown = set(db_fields) - valid_columns
        if unknown:
            raise ValueError(f"Unsupported job fields: {sorted(unknown)}")

        assignments = ", ".join(f"{column} = ?" for column in db_fields)
        values = [db_fields[column] for column in db_fields]
        values.append(job_id)

        with self._lock:
            self._conn.execute(
                f"UPDATE orchestration_jobs SET {assignments} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def append_progress(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO orchestration_progress (job_id, timestamp, name, status, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(event.get("timestamp") or ""),
                    str(event.get("name") or "unknown"),
                    str(event.get("status") or "running"),
                    event.get("details"),
                ),
            )
            self._conn.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM orchestration_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            progress_rows = self._conn.execute(
                """
                SELECT timestamp, name, status, details
                FROM orchestration_progress
                WHERE job_id = ?
                ORDER BY id ASC
                """,
                (job_id,),
            ).fetchall()

        return {
            "id": row["id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "error": row["error"],
            "request": self._from_json(row["request_json"]),
            "result": self._from_json(row["result_json"]),
            "progress": [
                {
                    "timestamp": event["timestamp"],
                    "name": event["name"],
                    "status": event["status"],
                    "details": event["details"],
                }
                for event in progress_rows
            ],
        }

    def list_jobs(self, limit: int = 20, include_progress: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            self._purge_old_jobs_locked(days=30)
            rows = self._conn.execute(
                """
                SELECT id, status, created_at, started_at, finished_at, error, request_json, result_json
                FROM orchestration_jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            progress_by_job: dict[str, list[dict[str, Any]]] = {}
            if include_progress and rows:
                job_ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in job_ids)
                progress_rows = self._conn.execute(
                    f"""
                    SELECT job_id, timestamp, name, status, details
                    FROM orchestration_progress
                    WHERE job_id IN ({placeholders})
                    ORDER BY id ASC
                    """,
                    job_ids,
                ).fetchall()
                for event in progress_rows:
                    progress_by_job.setdefault(event["job_id"], []).append(
                        {
                            "timestamp": event["timestamp"],
                            "name": event["name"],
                            "status": event["status"],
                            "details": event["details"],
                        }
                    )

        items: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": row["id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
                "request": self._from_json(row["request_json"]),
                "result": self._from_json(row["result_json"]),
            }
            if include_progress:
                item["progress"] = progress_by_job.get(row["id"], [])
            items.append(item)
        return items

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_history_store: HistoryStore | None = None
_history_store_lock = Lock()


def get_history_store() -> HistoryStore:
    global _history_store
    with _history_store_lock:
        if _history_store is None:
            db_path = os.environ.get("AGENT_FLOW_HISTORY_DB_PATH", "/tmp/agent_flow-orch-history.db")
            _history_store = HistoryStore(db_path=db_path)
        return _history_store


def reset_history_store_for_tests() -> None:
    global _history_store
    with _history_store_lock:
        if _history_store is not None:
            _history_store.close()
        _history_store = None