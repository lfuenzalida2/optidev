"""Cifrado de secretos guardados en la base (credenciales de las VMs, acceso
al Vault de preview). Fernet con VM_CREDENTIALS_KEY, o con una clave derivada
de JWT_SECRET si no está definida (ver settings)."""

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from src.config.settings import settings
from src.services.errors import ServiceError


def _fernet() -> Fernet:
    source = settings.VM_CREDENTIALS_KEY or f"optidev-vm-credentials:{settings.JWT_SECRET}"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest()))


def encrypt_json(secret: dict) -> str:
    return _fernet().encrypt(json.dumps(secret).encode()).decode()


def decrypt_json(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise ServiceError("No se pudieron leer las credenciales guardadas (¿cambió la clave de cifrado?); "
                           "volvé a cargarlas", 409) from exc
