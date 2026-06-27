"""SQLite-backed store for password-based users, roles, and session lifecycle."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

VALID_ROLES = ("admin", "developer", "user")


class AuthStore:
    """Persist users (with hashed passwords and roles) and authenticated sessions."""

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    company TEXT,
                    mobile_no TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_activity_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON user_sessions(user_id);
                """
            )
            self._conn.commit()

    @staticmethod
    def _to_user_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Public user payload (never exposes password_hash)."""
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "company": row["company"],
            "mobile_no": row["mobile_no"],
            "role": row["role"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
        }

    def count_users(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()
        return int(row["total"] if row else 0)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, name, email, company, mobile_no, role,
                       created_at, updated_at, last_login_at
                FROM users
                WHERE email = ?
                """,
                (email,),
            ).fetchone()
        return self._to_user_payload(row)

    def get_password_hash(self, email: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        return row["password_hash"] if row else None

    def create_user(
        self,
        *,
        name: str,
        email: str,
        company: str | None,
        mobile_no: str | None,
        role: str,
        password_hash: str,
        now_iso: str,
    ) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO users (name, email, company, mobile_no, role, password_hash,
                                   created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (name, email, company, mobile_no, role, password_hash, now_iso, now_iso),
            )
            self._conn.commit()
            created = self._conn.execute(
                """
                SELECT id, name, email, company, mobile_no, role,
                       created_at, updated_at, last_login_at
                FROM users
                WHERE email = ?
                """,
                (email,),
            ).fetchone()

        payload = self._to_user_payload(created)
        if payload is None:
            raise ValueError("Failed to persist user record")
        return payload

    def update_last_login(self, *, user_id: int, now_iso: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now_iso, now_iso, user_id),
            )
            self._conn.commit()

    def set_user_role(self, *, email: str, role: str, now_iso: str) -> dict[str, Any] | None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE email = ?",
                (role, now_iso, email),
            )
            self._conn.commit()
        return self.get_user_by_email(email)

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, name, email, company, mobile_no, role,
                       created_at, updated_at, last_login_at
                FROM users
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [payload for row in rows if (payload := self._to_user_payload(row)) is not None]

    def create_session(
        self,
        *,
        session_token: str,
        user_id: int,
        created_at: str,
        expires_at: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_sessions (session_token, user_id, created_at, last_activity_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_token, user_id, created_at, created_at, expires_at),
            )
            self._conn.commit()

    def touch_session(self, *, session_token: str, now_iso: str, expires_at: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE user_sessions
                SET last_activity_at = ?, expires_at = ?
                WHERE session_token = ?
                """,
                (now_iso, expires_at, session_token),
            )
            self._conn.commit()

    def delete_session(self, session_token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM user_sessions WHERE session_token = ?", (session_token,))
            self._conn.commit()

    def get_active_session_with_user(self, *, session_token: str, now_iso: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT s.session_token, s.user_id, s.created_at, s.last_activity_at, s.expires_at,
                       u.id AS user_id_payload, u.name, u.email, u.company, u.mobile_no, u.role,
                       u.created_at AS user_created_at, u.updated_at AS user_updated_at, u.last_login_at
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.session_token = ?
                  AND s.expires_at >= ?
                LIMIT 1
                """,
                (session_token, now_iso),
            ).fetchone()
            if row is None:
                return None

        return {
            "session": {
                "session_token": row["session_token"],
                "user_id": row["user_id"],
                "created_at": row["created_at"],
                "last_activity_at": row["last_activity_at"],
                "expires_at": row["expires_at"],
            },
            "user": {
                "id": row["user_id_payload"],
                "name": row["name"],
                "email": row["email"],
                "company": row["company"],
                "mobile_no": row["mobile_no"],
                "role": row["role"],
                "created_at": row["user_created_at"],
                "updated_at": row["user_updated_at"],
                "last_login_at": row["last_login_at"],
            },
        }

    def purge_expired_sessions(self, *, now_iso: str) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM user_sessions WHERE expires_at < ?", (now_iso,))
            self._conn.commit()
            return int(cursor.rowcount or 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _default_auth_db_path() -> str:
    return os.environ.get("AGENT_FLOW_AUTH_DB_PATH", "/tmp/agent_flow-auth.db")


def session_expiry_from(now: datetime) -> str:
    return (now + timedelta(days=1)).isoformat()


_auth_store: AuthStore | None = None
_auth_store_lock = Lock()


def get_auth_store() -> AuthStore:
    global _auth_store
    with _auth_store_lock:
        if _auth_store is None:
            _auth_store = AuthStore(db_path=_default_auth_db_path())
        return _auth_store


def reset_auth_store_for_tests() -> None:
    global _auth_store
    with _auth_store_lock:
        if _auth_store is not None:
            _auth_store.close()
        _auth_store = None
