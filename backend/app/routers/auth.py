"""Authentication router: password-based login/signup, roles, and 1-day sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.auth_store import VALID_ROLES, get_auth_store, session_expiry_from

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE_NAME = "agentflow_session"
SESSION_TIMEOUT_SECONDS = 24 * 60 * 60

# Roles that may trigger / cancel agentic workflows.
RUN_ROLES = frozenset({"admin", "developer"})

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SPECIAL_PATTERN = re.compile(r"[^A-Za-z0-9]")
_PBKDF2_ITERATIONS = 200_000


# ── Password hashing (stdlib only) ────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations_str, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def password_policy_error(password: str) -> str | None:
    """Return an error message if the password fails the strong-password policy."""
    if len(password) < 8:
        return "Password must be at least 8 characters long"
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter"
    if not re.search(r"\d", password):
        return "Password must include at least one number"
    if not _SPECIAL_PATTERN.search(password):
        return "Password must include at least one special character"
    return None


# ── Request models ────────────────────────────────────────────────────────────
class SignupPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    confirm_password: str = Field(min_length=8, max_length=200)
    company: str | None = Field(default=None, max_length=120)
    mobile_no: str | None = Field(default=None, max_length=40)


class LoginPayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class UpdateRolePayload(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=2, max_length=20)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _normalize_optional(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _is_valid_email(email: str) -> bool:
    return bool(_EMAIL_PATTERN.match(email))


def _auth_disabled() -> bool:
    return os.environ.get("AGENT_FLOW_DISABLE_AUTH", "0").strip().lower() in {"1", "true", "yes", "on"}


def _use_secure_cookie() -> bool:
    return os.environ.get("AUTH_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_use_secure_cookie(),
        samesite="lax",
        max_age=SESSION_TIMEOUT_SECONDS,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def resolve_user_from_session(session_token: str | None) -> dict | None:
    """Return the active user for a session token, extending the inactivity window."""
    if not session_token:
        return None

    now = _utcnow()
    now_iso = now.isoformat()
    store = get_auth_store()
    store.purge_expired_sessions(now_iso=now_iso)

    auth_payload = store.get_active_session_with_user(session_token=session_token, now_iso=now_iso)
    if not auth_payload:
        return None

    store.touch_session(
        session_token=session_token,
        now_iso=now_iso,
        expires_at=session_expiry_from(now),
    )
    return auth_payload["user"]


# ── Permission dependencies ───────────────────────────────────────────────────
def get_current_user(request: Request) -> dict:
    if _auth_disabled():
        return {"id": 0, "name": "Dev", "email": "dev@local", "role": "admin"}
    # Middleware sets request.state.user for protected /api routes, but it skips
    # /api/auth/* — so fall back to resolving the session cookie directly.
    user = getattr(request.state, "user", None)
    if not user:
        user = resolve_user_from_session(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_run_permission(request: Request) -> dict:
    user = get_current_user(request)
    if user.get("role") not in RUN_ROLES:
        raise HTTPException(status_code=403, detail="You do not have permission to run agentic workflows")
    return user


def require_admin(request: Request) -> dict:
    user = get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.post("/signup")
def signup(payload: SignupPayload, response: Response):
    email = _normalize_email(payload.email)
    if not _is_valid_email(email):
        raise HTTPException(status_code=422, detail="Invalid email format")

    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="Password and confirm password do not match")

    policy_error = password_policy_error(payload.password)
    if policy_error:
        raise HTTPException(status_code=400, detail=policy_error)

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    store = get_auth_store()
    if store.get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    # First registered account becomes admin; subsequent accounts default to 'user'.
    role = "admin" if store.count_users() == 0 else "user"

    now = _utcnow()
    user = store.create_user(
        name=name,
        email=email,
        company=_normalize_optional(payload.company),
        mobile_no=_normalize_optional(payload.mobile_no),
        role=role,
        password_hash=hash_password(payload.password),
        now_iso=now.isoformat(),
    )

    token = _start_session(store, user_id=int(user["id"]), now=now)
    _session_cookie(response, token)
    return {"authenticated": True, "user": user}


@router.post("/login")
def login(payload: LoginPayload, response: Response):
    email = _normalize_email(payload.email)
    store = get_auth_store()

    user = store.get_user_by_email(email)
    password_hash = store.get_password_hash(email)
    if not user or not verify_password(payload.password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    now = _utcnow()
    store.update_last_login(user_id=int(user["id"]), now_iso=now.isoformat())
    token = _start_session(store, user_id=int(user["id"]), now=now)
    _session_cookie(response, token)
    return {"authenticated": True, "user": user}


@router.get("/session")
def get_session(response: Response, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    user = resolve_user_from_session(session_token)
    if not user:
        _clear_session_cookie(response)
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


@router.post("/logout")
def logout(response: Response, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)):
    if session_token:
        get_auth_store().delete_session(session_token)
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/users")
def list_users(_admin: dict = Depends(require_admin)):
    return {"items": get_auth_store().list_users()}


@router.post("/users/role")
def update_user_role(payload: UpdateRolePayload, _admin: dict = Depends(require_admin)):
    role = payload.role.strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(VALID_ROLES)}")

    email = _normalize_email(payload.email)
    store = get_auth_store()
    if store.get_user_by_email(email) is None:
        raise HTTPException(status_code=404, detail="User not found")

    updated = store.set_user_role(email=email, role=role, now_iso=_utcnow().isoformat())
    return {"ok": True, "user": updated}


def _start_session(store, *, user_id: int, now: datetime) -> str:
    session_token = secrets.token_urlsafe(48)
    store.create_session(
        session_token=session_token,
        user_id=user_id,
        created_at=now.isoformat(),
        expires_at=session_expiry_from(now),
    )
    return session_token
