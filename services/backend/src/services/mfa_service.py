"""MFA con app de autenticación (TOTP, RFC 6238: Google Authenticator, Authy...).

Ingreso en dos pasos: la contraseña correcta no da el token de acceso sino un
token de MFA (JWT `typ=mfa`, 5 minutos) que solo sirve para registrar el MFA
o validar el código. Con el código válido se entrega el token de acceso.

- Registro: `start_setup` genera el secreto (cifrado con Fernet) y
  `verify` con el primer código lo da por activado. Hasta entonces se puede
  volver a empezar (secreto nuevo).
- Anti-reuso: se guarda el último paso de 30 s aceptado; ese código y los
  anteriores ya no sirven.
- Errores: token de MFA vencido/inválido = 401 (volver a la contraseña);
  código incorrecto = 400; bloqueado = 429.
- Fuerza bruta: MAX_ATTEMPTS códigos incorrectos seguidos bloquean el MFA del
  usuario por LOCK_MINUTES.
- Un admin lo rehace con `reset`: el usuario lo vuelve a registrar al entrar.

Los tokens personales `odv_...` (Claude Code / MCP) no pasan por MFA: se crean
desde una sesión que ya lo validó.
"""

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.models.user import User
from src.services.auth_service import ALGORITHM, get_active_user
from src.services.errors import ServiceError

MFA_TOKEN_TTL_MINUTES = 5
MAX_ATTEMPTS = 5
LOCK_MINUTES = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    # SQLite devuelve las fechas naive; siempre son UTC.
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _fernet() -> Fernet:
    source = settings.MFA_SECRET_KEY or f"optidev-mfa:{settings.JWT_SECRET}"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest()))


def _secret(user: User) -> str:
    try:
        return _fernet().decrypt(user.mfa_secret.encode()).decode()
    except InvalidToken as exc:
        raise ServiceError("No se pudo leer tu MFA (¿cambió la clave de cifrado?); pedile a un admin que lo rehaga",
                           409) from exc


def step_for(user: User) -> str:
    """Qué le falta al usuario después de la contraseña: `verify`, `setup` o nada (None)."""
    if user.mfa_enabled_at:
        return "verify"
    return "setup" if settings.MFA_REQUIRED else None


def make_mfa_token(user: User) -> str:
    now = _now()
    payload = {
        "userId": user.id,
        "typ": "mfa",
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=MFA_TOKEN_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


async def user_from_mfa_token(db: AsyncSession, token: str) -> User:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        payload = None
    user = await get_active_user(db, payload.get("userId")) if payload and payload.get("typ") == "mfa" else None
    if not user:
        raise ServiceError("La verificación expiró; volvé a ingresar tu contraseña", 401)
    return user


async def start_setup(db: AsyncSession, user: User) -> dict:
    if user.mfa_enabled_at:
        raise ServiceError("El MFA ya está registrado", 409)
    secret = pyotp.random_base32()
    user.mfa_secret = _fernet().encrypt(secret.encode()).decode()
    user.mfa_last_step = None
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name=settings.MFA_ISSUER)
    return {"secret": secret, "otpauthUri": uri}


def _check_lock(user: User) -> None:
    locked_until = _aware(user.mfa_locked_until)
    if locked_until and locked_until > _now():
        minutes = max(1, round((locked_until - _now()).total_seconds() / 60))
        raise ServiceError(f"Demasiados códigos incorrectos; probá de nuevo en {minutes} min", 429)


def _matching_step(secret: str, code: str, last_step: int | None) -> int | None:
    """Paso de 30 s al que corresponde el código, con ±1 de tolerancia por reloj
    desfasado. None si no coincide o ya se usó."""
    totp = pyotp.TOTP(secret)
    current = totp.timecode(_now())
    for step in (current - 1, current, current + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


async def verify(db: AsyncSession, user: User, code: str) -> None:
    """Valida el código; si el registro estaba pendiente, lo deja activado."""
    if not user.mfa_secret:
        raise ServiceError("Primero registrá el MFA", 409)
    _check_lock(user)
    code = "".join((code or "").split())
    step = _matching_step(_secret(user), code, user.mfa_last_step) if code.isdigit() and len(code) == 6 else None
    if step is None:
        user.mfa_failed_attempts = (user.mfa_failed_attempts or 0) + 1
        if user.mfa_failed_attempts >= MAX_ATTEMPTS:
            user.mfa_failed_attempts = 0
            user.mfa_locked_until = _now() + timedelta(minutes=LOCK_MINUTES)
        await db.commit()
        raise ServiceError("Código incorrecto", 400)
    user.mfa_last_step = step
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    if not user.mfa_enabled_at:
        user.mfa_enabled_at = _now()
    await db.commit()


async def reset(db: AsyncSession, user: User) -> None:
    user.mfa_secret = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    user.mfa_failed_attempts = 0
    user.mfa_locked_until = None
    await db.commit()
