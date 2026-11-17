"""Actualización de la app desde la UI (solo admins).

El backend no ejecuta nada privilegiado: el pedido es un archivo vacío que
detecta `optidev-update.path` (systemd), que corre `deploy/update.sh` como
root. El script ignora el contenido del archivo; acá
solo se lee el estado y el log que deja en UPDATE_STATE_DIR.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from src.config.settings import settings
from src.middleware.auth import AdminActor

router = APIRouter()
logger = logging.getLogger(__name__)

LOG_TAIL_BYTES = 32 * 1024
# Un "running" sin novedades por más que el TimeoutStartSec de la unidad (20
# min) es una corrida que murió sin poder escribir el estado final.
STALE_AFTER = timedelta(minutes=30)


def _request_dir() -> Path:
    return Path(settings.UPDATE_REQUEST_DIR)


def _state_dir() -> Path:
    return Path(settings.UPDATE_STATE_DIR)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LOG_TAIL_BYTES))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    if size > LOG_TAIL_BYTES:
        text = text.split("\n", 1)[-1]
    return text


def _is_running(status: dict | None) -> bool:
    if not status or status.get("state") != "running":
        return False
    try:
        updated = datetime.fromisoformat(str(status.get("updatedAt")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) - updated < STALE_AFTER


def _snapshot() -> dict:
    status = _read_json(_state_dir() / "status.json")
    running = _is_running(status)
    if status and status.get("state") == "running" and not running:
        status = {**status, "state": "failed", "message": "La actualización quedó colgada; revisar el servidor."}
    return {
        # Sin el directorio de estado la unidad systemd no está instalada.
        "available": _state_dir().is_dir(),
        "pending": (_request_dir() / "request").exists(),
        "running": running,
        "status": status,
        "lastRequest": _read_json(_request_dir() / "last_request.json"),
        "log": _log_tail(_state_dir() / "update.log"),
    }


@router.get("/update")
async def update_status(_: AdminActor):
    return {"success": True, "data": _snapshot()}


@router.post("/update", status_code=202)
async def request_update(actor: AdminActor):
    snapshot = _snapshot()
    if not snapshot["available"]:
        raise HTTPException(503, "La actualización automática no está instalada en el servidor")
    if snapshot["pending"] or snapshot["running"]:
        raise HTTPException(409, "Ya hay una actualización en curso")

    request_dir = _request_dir()
    request_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "requestedBy": actor.user.username,
        "requestedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # Para mostrar en la UI quién la pidió. El script no lee ninguno de los dos archivos.
    tmp = request_dir / "last_request.json.tmp"
    tmp.write_text(json.dumps(meta))
    tmp.replace(request_dir / "last_request.json")
    try:
        fd = os.open(request_dir / "request", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise HTTPException(409, "Ya hay una actualización en curso")
    os.close(fd)
    logger.info("Actualización pedida por %s", actor.user.username)
    return {"success": True, "data": _snapshot(), "message": "Actualización pedida"}
