"""SQLite-backed persistence for orchestration jobs and progress history."""

from __future__ import annotations

import json
import os
import shutil
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
        self._repo_base_dir = Path(os.environ.get("AGENT_FLOW_REPO_BASE_DIR", "/tmp/agent_flow-tmp-repos")).resolve()
        self._init_schema()

    def _extract_cleanup_paths(self, request_json: str | None, result_json: str | None) -> list[Path]:
        paths: set[Path] = set()
        payloads = [self._from_json(request_json), self._from_json(result_json)]
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            workspace = payload.get("workspace_dir")
            if isinstance(workspace, str) and workspace.strip():
                try:
                    paths.add(Path(workspace).resolve())
                except OSError:
                    continue
        return sorted(paths)

    def _safe_cleanup_paths(self, paths: list[Path]) -> None:
        for path in paths:
            try:
                if not path.is_absolute():
                    continue
                if not str(path).startswith(str(self._repo_base_dir)):
                    continue
                if path.exists() and path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    @staticmethod
    def _retention_cutoff_iso(days: int = 30) -> str:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return cutoff.isoformat()

    def _purge_old_jobs_locked(self, days: int = 30) -> int:
        cutoff = self._retention_cutoff_iso(days)
        old_job_rows = self._conn.execute(
            """
            SELECT id, request_json, result_json
            FROM orchestration_jobs
            WHERE created_at < ?
            """,
            (cutoff,),
        ).fetchall()
        if not old_job_rows:
            return 0

        job_ids = [row["id"] for row in old_job_rows]
        cleanup_paths: list[Path] = []
        for row in old_job_rows:
            cleanup_paths.extend(self._extract_cleanup_paths(row["request_json"], row["result_json"]))
        placeholders = ",".join("?" for _ in job_ids)
        self._conn.execute(
            f"DELETE FROM orchestration_progress WHERE job_id IN ({placeholders})",
            job_ids,
        )
        self._conn.execute(
            f"DELETE FROM orchestration_jobs WHERE id IN ({placeholders})",
            job_ids,
        )
        self._safe_cleanup_paths(cleanup_paths)
        return len(job_ids)

    def purge_old_jobs(self, days: int = 30) -> int:
        with self._lock:
            deleted = self._purge_old_jobs_locked(days)
            if deleted:
                self._conn.commit()
            return deleted

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, request_json, result_json FROM orchestration_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False

            cleanup_paths = self._extract_cleanup_paths(row["request_json"], row["result_json"])
            self._conn.execute("DELETE FROM orchestration_progress WHERE job_id = ?", (job_id,))
            self._conn.execute("DELETE FROM orchestration_jobs WHERE id = ?", (job_id,))
            self._safe_cleanup_paths(cleanup_paths)
            self._conn.commit()
            return True

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

                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    selected_model TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT,
                    FOREIGN KEY(session_id) REFERENCES chat_sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
                ON chat_sessions(updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                ON chat_messages(session_id, created_at ASC);
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

    def create_chat_session(
        self,
        *,
        session_id: str,
        title: str,
        mode: str,
        selected_model: str | None,
        created_at: str,
        metadata: dict[str, Any],
    ) -> None:
        metadata_json = self._to_json(metadata)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO chat_sessions (
                    id, title, status, mode, selected_model, created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    title,
                    "open",
                    mode,
                    selected_model,
                    created_at,
                    created_at,
                    metadata_json,
                ),
            )
            self._conn.commit()

    def list_chat_sessions(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, title, status, mode, selected_model, created_at, updated_at, metadata_json
                FROM chat_sessions
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        sessions: list[dict[str, Any]] = []
        for row in rows:
            sessions.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "mode": row["mode"],
                    "selected_model": row["selected_model"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "metadata": self._from_json(row["metadata_json"]) or {},
                }
            )
        return sessions

    def get_chat_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, title, status, mode, selected_model, created_at, updated_at, metadata_json
                FROM chat_sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "mode": row["mode"],
            "selected_model": row["selected_model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": self._from_json(row["metadata_json"]) or {},
        }

    def update_chat_session(self, session_id: str, **fields: Any) -> bool:
        if not fields:
            return False

        db_fields = dict(fields)
        if "metadata" in db_fields:
            db_fields["metadata_json"] = self._to_json(db_fields.pop("metadata"))

        valid_columns = {
            "title",
            "status",
            "mode",
            "selected_model",
            "updated_at",
            "metadata_json",
        }
        unknown = set(db_fields) - valid_columns
        if unknown:
            raise ValueError(f"Unsupported chat session fields: {sorted(unknown)}")

        assignments = ", ".join(f"{column} = ?" for column in db_fields)
        values = [db_fields[column] for column in db_fields]
        values.append(session_id)

        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE chat_sessions SET {assignments} WHERE id = ?",
                values,
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def append_chat_message(
        self,
        *,
        message_id: str,
        session_id: str,
        role: str,
        kind: str,
        content: str,
        created_at: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        payload_json = self._to_json(payload) if payload is not None else None
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO chat_messages (
                    id, session_id, role, kind, content, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, kind, content, created_at, payload_json),
            )
            self._conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (created_at, session_id),
            )
            self._conn.commit()

    def list_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, session_id, role, kind, content, created_at, payload_json
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()

        messages: list[dict[str, Any]] = []
        for row in rows:
            messages.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "role": row["role"],
                    "kind": row["kind"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "payload": self._from_json(row["payload_json"]) if row["payload_json"] else None,
                }
            )
        return messages

    def archive_chat_session(self, session_id: str, archived_at: str) -> bool:
        return self.update_chat_session(session_id, status="closed", updated_at=archived_at)


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