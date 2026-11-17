import asyncio
import json

import asyncssh
import pytest
from fastapi import WebSocketDisconnect
from starlette.testclient import TestClient

from src.app import app
from src.services import terminal_service


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "qa" and password == "secret"


async def _shell(process):
    """Shell de juguete: informa la terminal pedida y repite cada línea."""
    cols, rows = process.get_terminal_size()[:2]
    process.stdout.write(f"term={process.get_terminal_type()} size={cols}x{rows}\r\n")
    while True:
        try:
            line = await process.stdin.readline()
        except asyncssh.TerminalSizeChanged:
            continue  # asyncssh avisa el resize interrumpiendo la lectura
        if not line or line.strip() == "exit":
            break
        if line.strip() == "size":
            cols, rows = process.get_terminal_size()[:2]
            process.stdout.write(f"size={cols}x{rows}\r\n")
        else:
            process.stdout.write(f"eco:{line.strip()}\r\n")
    process.exit(3)


@pytest.fixture
async def ssh_port():
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=_shell,
    )
    yield server.sockets[0].getsockname()[1]
    server.close()


class FakeWebSocket:
    """Lo mínimo de starlette.WebSocket que usa terminal_service, con colas:
    permite manejar run_terminal en el mismo event loop que el servidor SSH."""

    def __init__(self):
        self.incoming = asyncio.Queue()
        self.text, self.output = [], bytearray()
        self.closed = None

    async def accept(self):
        pass

    async def receive_text(self):
        message = await self.incoming.get()
        if message is None:
            raise WebSocketDisconnect(1000)
        return message

    async def send_text(self, data):
        self.text.append(json.loads(data))

    async def send_bytes(self, data):
        self.output.extend(data)

    async def close(self, code=1000, reason=None):
        self.closed = code

    def send(self, **message):
        self.incoming.put_nowait(json.dumps(message))

    async def wait_output(self, text, timeout=5):
        async def poll():
            while text not in self.output.decode(errors="replace"):
                await asyncio.sleep(0.02)
        await asyncio.wait_for(poll(), timeout)


async def _token(admin):
    return admin.headers["Authorization"].removeprefix("Bearer ")


async def _vm(admin, port, password="secret"):
    resp = await admin.post("/api/vms", json={"host": "127.0.0.1", "port": port, "username": "qa",
                                              "authType": "password", "password": password})
    return resp.json()["data"]


async def test_terminal_session(admin, ssh_port):
    vm = await _vm(admin, ssh_port)
    ws = FakeWebSocket()
    ws.send(type="auth", token=await _token(admin), cols=120, rows=40)
    task = asyncio.create_task(terminal_service.run_terminal(ws, vm["id"]))

    await ws.wait_output("term=xterm-256color size=120x40")
    assert ws.text[0] == {"type": "connected", "target": f"qa@127.0.0.1:{ssh_port}"}

    ws.send(type="input", data="hola\r")
    await ws.wait_output("eco:hola")
    ws.send(type="resize", cols=90, rows=20)
    ws.send(type="ping")
    ws.send(type="input", data="size\r")
    await ws.wait_output("size=90x20")

    # Salir del shell cierra la terminal con el código de salida.
    ws.send(type="input", data="exit\r")
    await asyncio.wait_for(task, 5)
    assert ws.text[-1] == {"type": "closed", "exitStatus": 3}
    assert ws.closed == 1000

    # La primera conexión fijó la clave del host.
    vms = (await admin.get("/api/vms")).json()["data"]
    assert vms[0]["hostKeyFingerprint"].startswith("SHA256:")


async def test_terminal_browser_closes(admin, ssh_port):
    vm = await _vm(admin, ssh_port)
    ws = FakeWebSocket()
    ws.send(type="auth", token=await _token(admin))
    task = asyncio.create_task(terminal_service.run_terminal(ws, vm["id"]))
    await ws.wait_output("size=80x24")  # sin tamaño en el auth: 80x24
    ws.incoming.put_nowait(None)  # el navegador cierra la pestaña
    await asyncio.wait_for(task, 5)
    assert ws.text == [{"type": "connected", "target": f"qa@127.0.0.1:{ssh_port}"}]


async def test_terminal_rejections(admin, ssh_port):
    async def run(vm_id, **auth):
        ws = FakeWebSocket()
        ws.send(type="auth", **auth)
        await asyncio.wait_for(terminal_service.run_terminal(ws, vm_id), 15)
        return ws

    vm = await _vm(admin, ssh_port)
    token = await _token(admin)

    ws = await run(vm["id"], token="no-es-un-token")
    assert ws.closed == terminal_service.CLOSE_UNAUTHORIZED

    ws = await run(9999, token=token)
    assert ws.closed == terminal_service.CLOSE_NOT_FOUND

    # La VM de otro usuario se trata como inexistente.
    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    ws = await run(vm["id"], token=login.json()["data"]["token"])
    assert ws.closed == terminal_service.CLOSE_NOT_FOUND

    bad = await _vm(admin, ssh_port, password="mala")
    ws = await run(bad["id"], token=token)
    assert ws.closed == terminal_service.CLOSE_SSH_ERROR
    assert "rechazó" in ws.text[-1]["message"]

    # Un token personal (Claude Code) también sirve.
    created = (await admin.post("/api/tokens", json={"name": "t"})).json()["data"]
    ws = FakeWebSocket()
    ws.send(type="auth", token=created["token"])
    task = asyncio.create_task(terminal_service.run_terminal(ws, vm["id"]))
    await ws.wait_output("size=80x24")
    ws.incoming.put_nowait(None)
    await asyncio.wait_for(task, 5)


def test_terminal_route_requires_auth_first():
    # Sin tocar la BD: un primer mensaje que no es de autenticación cierra el socket.
    with TestClient(app).websocket_connect("/api/vms/1/terminal") as ws:
        ws.send_text(json.dumps({"type": "input", "data": "ls\r"}))
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == terminal_service.CLOSE_UNAUTHORIZED


async def test_admin_opens_terminal_on_another_users_vm(admin, ssh_port):
    created = await admin.post("/api/users", json={"username": "ana", "email": "ana@optidev.com", "name": "Ana",
                                                   "password": "ana-pass-123"})
    vm = (await admin.post("/api/vms", json={"host": "127.0.0.1", "port": ssh_port, "username": "qa",
                                             "authType": "password", "password": "secret",
                                             "userId": created.json()["data"]["id"]})).json()["data"]

    ws = FakeWebSocket()
    ws.send(type="auth", token=await _token(admin))
    task = asyncio.create_task(terminal_service.run_terminal(ws, vm["id"]))
    await ws.wait_output("size=80x24")
    assert ws.text[0] == {"type": "connected", "target": f"qa@127.0.0.1:{ssh_port}"}
    ws.incoming.put_nowait(None)
    await asyncio.wait_for(task, 5)

    # Otro usuario común sigue sin poder entrar a la VM de ana.
    await admin.post("/api/users", json={"username": "beto", "email": "beto@optidev.com", "name": "Beto",
                                         "password": "beto-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "beto", "password": "beto-pass-123"})
    ws = FakeWebSocket()
    ws.send(type="auth", token=login.json()["data"]["token"])
    await asyncio.wait_for(terminal_service.run_terminal(ws, vm["id"]), 15)
    assert ws.closed == terminal_service.CLOSE_NOT_FOUND
