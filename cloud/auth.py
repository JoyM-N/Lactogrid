"""API keys for ingest, and signed tokens for people.

The development secret and passwords live in the environment, not in git.
Hashes are stored. A token is ``user_id|role|expiry|hmac``.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.enums import Role
from shared.models import ApiKeyRow, UserRow


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def hash_password(password: str, salt: bytes | None = None) -> str:
    import os

    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def issue_token(user_id: str, role: str, secret: str, ttl_seconds: int = 12 * 3600) -> str:
    expiry = int(time.time()) + ttl_seconds
    body = f"{user_id}|{role}|{expiry}"
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}|{signature}"


def parse_token(token: str, secret: str) -> tuple[str, str]:
    parts = token.split("|")
    if len(parts) != 4:
        raise HTTPException(status_code=401, detail="invalid token")
    user_id, role, expiry, signature = parts
    body = f"{user_id}|{role}|{expiry}"
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid token")
    if int(expiry) < int(time.time()):
        raise HTTPException(status_code=401, detail="token expired")
    return user_id, role


def get_session(request: Request):
    session = Session(request.app.state.engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_api_key(
    session: Session = Depends(get_session),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ApiKeyRow:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing api key")
    row = session.scalar(
        select(ApiKeyRow).where(
            ApiKeyRow.key_hash == hash_api_key(x_api_key),
            ApiKeyRow.revoked_at.is_(None),
        )
    )
    if row is None:
        raise HTTPException(status_code=401, detail="invalid api key")
    return row


def require_user(
    request: Request,
    session: Session = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> UserRow:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    user_id, _role = parse_token(authorization.split(" ", 1)[1], request.app.state.secret)
    user = session.get(UserRow, user_id)
    if user is None or user.disabled:
        raise HTTPException(status_code=401, detail="unknown user")
    return user


def can_read_site(user: UserRow, cooperative_id: str) -> bool:
    if user.role == Role.ADMIN.value:
        return True
    return bool(user.cooperative_id) and user.cooperative_id == cooperative_id


def require_writer(user: UserRow) -> None:
    if user.role == Role.VIEWER.value:
        raise HTTPException(status_code=403, detail="viewers cannot change records")


def require_admin(user: UserRow) -> None:
    if user.role != Role.ADMIN.value:
        raise HTTPException(status_code=403, detail="admin only")
