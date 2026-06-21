"""JWT-based authentication + role-based access control (RBAC).

Real, DB-backed auth: credentials are verified against the SQLite `users`
table (see app/users.py) and the issued JWT carries the user's role.

Endpoints
─────────
    POST /auth/token   {username, password}  (OAuth2 form)
        →  {access_token, token_type, role, username}
    GET  /auth/me      (Bearer)
        →  {username, role}

Dependencies
────────────
    require_auth   → decoded subject (or None when auth disabled)
    current_user   → {"username", "role"} dict (raises 401 if missing/invalid)
    require_admin  → current_user, but raises 403 unless role == "admin"

`require_auth` stays a no-op when AGENTIC_AUTH_ENABLED is false so existing
analysis endpoints keep working without a token in dev/CI.  The login flow and
RBAC dependencies always work regardless of that flag, because the React
dashboard relies on the role returned by /auth/token to gate the UI.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

from app.config import settings

log = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_MINUTES = 60 * 8  # 8 hours

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def _make_token(subject: str, role: str) -> str:
    try:
        from jose import jwt
    except ImportError:
        raise RuntimeError("python-jose not installed — add it to requirements.txt")

    expire = datetime.now(timezone.utc) + timedelta(minutes=_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": subject, "role": role, "exp": expire},
        settings.auth_secret.get_secret_value(),
        algorithm=_ALGORITHM,
    )


def _decode_token(token: str | None) -> dict:
    """Decode and validate a bearer token. Returns the full claims dict."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        from jose import jwt

        payload = jwt.decode(
            token,
            settings.auth_secret.get_secret_value(),
            algorithms=[_ALGORITHM],
        )
        if not payload.get("sub"):
            raise ValueError("missing sub claim")
        return payload
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── dependencies ──────────────────────────────────────────────────────────────

def require_auth(token: Annotated[str | None, Depends(oauth2_scheme)]) -> str | None:
    """Soft auth gate. No-op when AGENTIC_AUTH_ENABLED is false."""
    if not settings.auth_enabled:
        return None
    return _decode_token(token)["sub"]


def current_user(token: Annotated[str | None, Depends(oauth2_scheme)]) -> dict:
    """Hard auth gate — always requires a valid token. Returns {username, role}."""
    claims = _decode_token(token)
    return {"username": claims["sub"], "role": claims.get("role", "user")}


def require_admin(user: Annotated[dict, Depends(current_user)]) -> dict:
    """RBAC gate — 403 unless the caller's role is 'admin'."""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for this resource",
        )
    return user


# ── router ────────────────────────────────────────────────────────────────────

auth_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_router.post("/token")
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()]) -> dict:
    """Exchange username+password for a bearer token + role (verified vs the DB)."""
    store = getattr(request.app.state, "users", None)
    if store is None:
        raise HTTPException(503, "User store is not initialised")

    rec = store.authenticate(form.username, form.password)
    if not rec:
        log.warning("auth: failed login for user=%s", form.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = _make_token(rec["username"], rec["role"])
    log.info("auth: issued token for user=%s role=%s", rec["username"], rec["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": rec["role"],
        "username": rec["username"],
    }


@auth_router.get("/me")
def me(user: Annotated[dict, Depends(current_user)]) -> dict:
    """Return the authenticated caller's identity + role (token introspection)."""
    return user
