"""Authentication API: register / login / logout / me.

Thin HTTP layer over :mod:`services.agent.store`. Issues opaque bearer tokens (stored
server-side so they can be revoked on logout) and resolves them back to a user. The
copilot works anonymously too; logging in simply switches conversation ownership to a
durable ``user:<id>`` bucket (see ``api/agent_routes.py``).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from services.agent import store

logger = logging.getLogger(__name__)

router = APIRouter()

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_MIN_PASSWORD = 6


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=_MIN_PASSWORD, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class AuthResponse(BaseModel):
    token: Optional[str] = None
    user: Optional[dict[str, Any]] = None
    error: Optional[str] = None


def bearer_token(authorization: Optional[str]) -> str:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def resolve_user(authorization: Optional[str]) -> Optional[dict[str, Any]]:
    """Resolve the current user from an Authorization header, or ``None``."""
    token = bearer_token(authorization)
    if not token:
        return None
    conn = store.store_connect()
    try:
        return store.user_for_token(conn, token)
    finally:
        conn.close()


@router.post("/api/auth/register", response_model=AuthResponse)
def register(req: RegisterRequest) -> AuthResponse:
    if not _USERNAME_RE.match(req.username):
        return AuthResponse(error="用户名需为 3-32 位字母/数字/._- 组合")
    conn = store.store_connect()
    try:
        try:
            user = store.create_user(conn, req.username, req.password)
        except store.UserExistsError:
            return AuthResponse(error="用户名已被占用")
        token = store.create_token(conn, user["id"])
        return AuthResponse(token=token, user=user)
    finally:
        conn.close()


@router.post("/api/auth/login", response_model=AuthResponse)
def login(req: LoginRequest) -> AuthResponse:
    conn = store.store_connect()
    try:
        user = store.authenticate(conn, req.username, req.password)
        if user is None:
            return AuthResponse(error="用户名或密码错误")
        token = store.create_token(conn, user["id"])
        return AuthResponse(token=token, user=user)
    finally:
        conn.close()


@router.post("/api/auth/logout", response_model=AuthResponse)
def logout(authorization: Optional[str] = Header(default=None)) -> AuthResponse:
    token = bearer_token(authorization)
    if token:
        conn = store.store_connect()
        try:
            store.delete_token(conn, token)
        finally:
            conn.close()
    return AuthResponse()


@router.get("/api/auth/me", response_model=AuthResponse)
def me(authorization: Optional[str] = Header(default=None)) -> AuthResponse:
    return AuthResponse(user=resolve_user(authorization))
