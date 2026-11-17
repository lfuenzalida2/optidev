"""Imágenes subidas desde la app (pegadas en descripciones y comentarios,
fotos de perfil).

Se guardan en disco (`UPLOAD_DIR`) con un nombre aleatorio de 128 bits y se
sirven en `/api/uploads/<nombre>` sin autenticación: un `<img>` no puede
mandar el header `Authorization`, así que el nombre impredecible hace de
credencial (quien tiene el link ve la imagen). Subir sí requiere sesión.

El tipo se valida por los bytes del archivo, no por lo que declara el
navegador. No se aceptan SVG: pueden llevar JavaScript.
"""

import re
import secrets
from pathlib import Path

from src.config.settings import settings
from src.services.errors import NotFoundError, ServiceError

URL_PREFIX = "/api/uploads/"

# Firma de cada formato → (extensión, content-type).
_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)
CONTENT_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}

_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$")
_REFERENCE = re.compile(re.escape(URL_PREFIX) + r"([0-9a-f]{32}\.(?:png|jpg|gif|webp))")


def _detect(data: bytes) -> str | None:
    for magic, ext, _ in _SIGNATURES:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _dir() -> Path:
    path = Path(settings.UPLOAD_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_image(data: bytes) -> dict:
    max_bytes = settings.UPLOAD_MAX_MB * 1024 * 1024
    if not data:
        raise ServiceError("El archivo está vacío")
    if len(data) > max_bytes:
        raise ServiceError(f"La imagen supera el máximo de {settings.UPLOAD_MAX_MB} MB", 413)
    ext = _detect(data)
    if not ext:
        raise ServiceError("Formato no soportado: solo PNG, JPEG, GIF o WebP", 415)
    name = f"{secrets.token_hex(16)}.{ext}"
    (_dir() / name).write_bytes(data)
    return {"name": name, "url": URL_PREFIX + name, "contentType": CONTENT_TYPES[ext], "size": len(data)}


def path_for(name: str) -> Path:
    """Ruta en disco de una imagen subida; valida el nombre para que no se
    pueda salir del directorio (`../`)."""
    path = Path(settings.UPLOAD_DIR) / name
    if not _NAME.match(name) or not path.is_file():
        raise NotFoundError("Imagen no encontrada")
    return path


def referenced_images(*texts: str) -> list[str]:
    """Nombres de las imágenes subidas que aparecen en los textos (markdown), sin repetir y en orden."""
    seen: dict[str, None] = {}
    for text in texts:
        for name in _REFERENCE.findall(text or ""):
            seen.setdefault(name)
    return list(seen)
