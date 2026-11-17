"""Terminal web a la VM de pruebas del usuario: un WebSocket que hace de puente
con una sesión SSH interactiva (asyncssh, con PTY).

Protocolo (`/api/vms/{id}/terminal`):
  - El navegador no puede mandar `Authorization` en un WebSocket y el token en
    la URL quedaría en los logs de nginx/uvicorn. Por eso el primer mensaje del
    cliente es `{"type": "auth", "token": "...", "cols": N, "rows": N}`; hasta
    validarlo no se lee la VM ni se abre ninguna conexión.
  - Cliente → servidor, siempre JSON: `input` (`data`: texto tecleado),
    `resize` (`cols`, `rows`) y `ping` (mantiene viva la conexión a través de
    nginx cuando la terminal está quieta).
  - Servidor → cliente: la salida de la terminal va en frames **binarios** (bytes
    crudos; xterm los decodifica) y los avisos en frames de texto JSON:
    `connected`, `error` (`message`) y `closed` (`exitStatus`). Al ir por canales
    distintos, una salida que parezca JSON nunca se confunde con un aviso.

La sesión se corta cuando vence el JWT con el que se abrió (los tokens
personales no vencen). La sesión de BD se usa solo al abrir: no queda tomada
mientras dura la terminal.
"""

import asyncio
import json
import logging
import time

import asyncssh
from fastapi import WebSocket, WebSocketDisconnect

from src.config.database import AsyncSessionLocal
from src.models.test_vm import TestVm
from src.services import vm_service
from src.services.auth_service import Actor, decode_access_token, resolve_bearer
from src.services.errors import ServiceError

logger = logging.getLogger(__name__)

AUTH_TIMEOUT_SECONDS = 10
READ_CHUNK = 65536
# Códigos de cierre de aplicación (4000-4999), análogos a HTTP.
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_SSH_ERROR = 4502


def _size(message: dict) -> tuple[int, int]:
    def clamp(value, default):
        try:
            return min(max(int(value), 1), 1000)
        except (TypeError, ValueError):
            return default
    return clamp(message.get("cols"), 80), clamp(message.get("rows"), 24)


async def _fail(websocket: WebSocket, code: int, message: str) -> None:
    try:
        await websocket.send_text(json.dumps({"type": "error", "message": message}))
        await websocket.close(code=code, reason=message[:120])
    except (RuntimeError, WebSocketDisconnect):
        pass


async def _open(websocket: WebSocket, vm_id: int):
    """Autentica el primer mensaje, carga la VM del usuario y arma la conexión.
    Devuelve (actor, vm, opciones de connect, cols, rows, vencimiento) o None si
    ya cerró el socket."""
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS)
        message = json.loads(raw)
    except asyncio.TimeoutError:
        await _fail(websocket, CLOSE_UNAUTHORIZED, "Tiempo de autenticación agotado")
        return None
    except (json.JSONDecodeError, TypeError):
        message = None
    except WebSocketDisconnect:
        return None
    if not isinstance(message, dict) or message.get("type") != "auth" or not isinstance(message.get("token"), str):
        await _fail(websocket, CLOSE_UNAUTHORIZED, "El primer mensaje debe ser de autenticación")
        return None

    token = message["token"]
    async with AsyncSessionLocal() as db:
        resolved = await resolve_bearer(db, token)
        if not resolved:
            await _fail(websocket, CLOSE_UNAUTHORIZED, "Sesión vencida o token inválido")
            return None
        actor = Actor(user=resolved[0], via=resolved[1])
        try:
            vm = await vm_service.get_vm(db, actor, vm_id, admin_any=True)
            options = vm_service.connect_options(vm)
        except ServiceError as exc:
            await _fail(websocket, CLOSE_NOT_FOUND if exc.status_code == 404 else CLOSE_SSH_ERROR, exc.message)
            return None

    payload = None if resolved[1] == "api" else decode_access_token(token)
    expires_at = float(payload["exp"]) if payload else None
    cols, rows = _size(message)
    return actor, vm, options, cols, rows, expires_at


async def _save_host_key(vm: TestVm, conn: asyncssh.SSHClientConnection) -> None:
    if not vm.host_key:
        async with AsyncSessionLocal() as db:
            stored = await db.get(TestVm, vm.id)
            if stored and vm_service.remember_host_key(stored, conn):
                await db.commit()


async def run_terminal(websocket: WebSocket, vm_id: int) -> None:
    await websocket.accept()
    opened = await _open(websocket, vm_id)
    if not opened:
        return
    actor, vm, options, cols, rows, expires_at = opened
    target = f"{vm.username}@{vm.host}:{vm.port}"

    try:
        conn = await asyncssh.connect(**options)
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        await _fail(websocket, CLOSE_SSH_ERROR, vm_service.connection_error(vm, exc))
        return

    logger.info("Terminal abierta: %s → %s (vm %s, dueño %s)", actor.user.username, target, vm.id, vm.user_id)
    async with conn:
        await _save_host_key(vm, conn)
        process = await conn.create_process(term_type="xterm-256color", term_size=(cols, rows),
                                            encoding=None, stderr=asyncssh.STDOUT)
        await websocket.send_text(json.dumps({"type": "connected", "target": target}))

        async def pump_output():
            while chunk := await process.stdout.read(READ_CHUNK):
                await websocket.send_bytes(chunk)

        async def pump_input():
            while True:
                try:
                    message = json.loads(await websocket.receive_text())
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "input" and isinstance(message.get("data"), str):
                    process.stdin.write(message["data"].encode())
                elif message.get("type") == "resize":
                    process.change_terminal_size(*_size(message))

        async def expire():
            if expires_at is None:
                await asyncio.Event().wait()
            await asyncio.sleep(max(0.0, expires_at - time.time()))

        tasks = {
            "output": asyncio.create_task(pump_output()),
            "input": asyncio.create_task(pump_input()),
            "expire": asyncio.create_task(expire()),
        }
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # El socket cerrado por el navegador llega como excepción de una de las
        # tareas: es el fin normal de la sesión, no un error.
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        process.close()

        try:
            if tasks["expire"] in done:
                await _fail(websocket, CLOSE_UNAUTHORIZED, "La sesión de optidev venció; volvé a iniciar sesión")
            elif tasks["output"] in done:
                await process.wait_closed()
                await websocket.send_text(json.dumps({"type": "closed", "exitStatus": process.exit_status}))
                await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass  # el navegador ya cerró
    logger.info("Terminal cerrada: %s → %s (vm %s)", actor.user.username, target, vm.id)
