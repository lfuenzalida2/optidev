import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.models.api_token import ApiToken
from src.models.user import User

ALGORITHM = "HS256"
API_TOKEN_PREFIX = "odv_"


@dataclass
class Actor:
    """Quién hace un cambio y por dónde entró (`web`, `api` o `mcp`)."""

    user: User
    via: str = "web"


def make_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "userId": user.id,
        "username": user.username,
        "role": user.role,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == "access" else None


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_api_token() -> str:
    return API_TOKEN_PREFIX + secrets.token_urlsafe(32)


async def get_active_user(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None), User.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def resolve_api_token(db: AsyncSession, raw: str) -> User | None:
    result = await db.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_api_token(raw), ApiToken.revoked_at.is_(None))
    )
    token = result.scalar_one_or_none()
    if not token:
        return None
    user = await get_active_user(db, token.user_id)
    if user:
        token.last_used_at = datetime.now(timezone.utc)
        await db.commit()
    return user


async def resolve_bearer(db: AsyncSession, raw: str) -> tuple[User, str] | None:
    """Acepta tanto el JWT de la UI como un token personal `odv_...`.
    Devuelve el usuario y el canal (`web` para JWT, `api` para token; el
    servidor MCP lo pisa con `mcp`)."""
    if raw.startswith(API_TOKEN_PREFIX):
        user = await resolve_api_token(db, raw)
        return (user, "api") if user else None
    payload = decode_access_token(raw)
    if not payload:
        return None
    user = await get_active_user(db, payload.get("userId"))
    return (user, "web") if user else None
