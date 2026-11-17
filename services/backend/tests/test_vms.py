import asyncssh
import pytest

from src.config.database import AsyncSessionLocal
from src.models.test_vm import TestVm

CLIENT_KEY = asyncssh.generate_private_key("ssh-ed25519")


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == "qa" and password == "secret"


async def _uname(process):
    process.stdout.write("Linux 6.1.0 x86_64\n")
    process.exit(0)


async def _ssh_server(host_key):
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=_uname,
        authorized_client_keys=asyncssh.import_authorized_keys(
            CLIENT_KEY.export_public_key("openssh").decode()),
    )
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture
async def ssh_server():
    server, port = await _ssh_server(asyncssh.generate_private_key("ssh-ed25519"))
    yield port
    server.close()


async def _member(admin):
    await admin.post("/api/users", json={
        "username": "dev", "email": "dev@optidev.com", "name": "Dev", "password": "dev-pass-123",
    })
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    return {"Authorization": f"Bearer {login.json()['data']['token']}"}


async def test_crud_hides_secrets_and_is_per_user(admin):
    resp = await admin.post("/api/vms", json={"name": "QA", "host": "10.0.0.5", "username": "qa",
                                              "authType": "password", "password": "secret"})
    assert resp.status_code == 201, resp.text
    vm = resp.json()["data"]
    assert (vm["host"], vm["port"], vm["authType"]) == ("10.0.0.5", 22, "password")
    assert "secret" not in resp.text and "password" not in vm

    async with AsyncSessionLocal() as db:
        stored = await db.get(TestVm, vm["id"])
        assert "secret" not in stored.secret_enc

    # Otro usuario no la ve ni la puede tocar.
    member = await _member(admin)
    assert (await admin.get("/api/vms", headers=member)).json()["data"] == []
    assert (await admin.patch(f"/api/vms/{vm['id']}", json={"name": "x"}, headers=member)).status_code == 404
    assert (await admin.delete(f"/api/vms/{vm['id']}", headers=member)).status_code == 404

    # Editar sin secreto conserva el guardado; cambiar a clave lo exige.
    resp = await admin.patch(f"/api/vms/{vm['id']}", json={"name": "QA 2"})
    assert resp.json()["data"]["name"] == "QA 2"
    resp = await admin.patch(f"/api/vms/{vm['id']}", json={"authType": "key"})
    assert resp.status_code == 400 and "clave privada" in resp.json()["detail"]

    assert (await admin.delete(f"/api/vms/{vm['id']}")).status_code == 200
    assert (await admin.get("/api/vms")).json()["data"] == []


async def test_validation(admin):
    base = {"host": "10.0.0.5", "username": "qa"}
    resp = await admin.post("/api/vms", json={**base, "authType": "password"})
    assert resp.status_code == 400 and "contraseña" in resp.json()["detail"]
    resp = await admin.post("/api/vms", json={**base, "authType": "key", "privateKey": "no es una clave"})
    assert resp.status_code == 400 and "clave privada" in resp.json()["detail"]
    resp = await admin.post("/api/vms", json={**base, "host": "10.0.0.5 ; rm", "authType": "password",
                                              "password": "x"})
    assert resp.status_code == 400
    resp = await admin.post("/api/vms", json={**base, "port": 70000, "authType": "password", "password": "x"})
    assert resp.status_code == 422

    encrypted = CLIENT_KEY.export_private_key("openssh", passphrase="frase").decode()
    resp = await admin.post("/api/vms", json={**base, "authType": "key", "privateKey": encrypted,
                                              "passphrase": "otra"})
    assert resp.status_code == 400
    resp = await admin.post("/api/vms", json={**base, "authType": "key", "privateKey": encrypted,
                                              "passphrase": "frase"})
    assert resp.status_code == 201, resp.text


async def test_check_connection_password_and_host_key(admin, ssh_server):
    vm = (await admin.post("/api/vms", json={"host": "127.0.0.1", "port": ssh_server, "username": "qa",
                                             "authType": "password", "password": "secret"})).json()["data"]
    checked = (await admin.post(f"/api/vms/{vm['id']}/check")).json()["data"]
    assert checked["lastCheckOk"] is True, checked["lastCheckMessage"]
    assert checked["lastCheckMessage"] == "Linux 6.1.0 x86_64"
    assert checked["hostKeyFingerprint"].startswith("SHA256:")

    # Contraseña incorrecta: queda registrado el error, no se rompe la request.
    await admin.patch(f"/api/vms/{vm['id']}", json={"password": "mala"})
    checked = (await admin.post(f"/api/vms/{vm['id']}/check")).json()["data"]
    assert checked["lastCheckOk"] is False and "rechazó" in checked["lastCheckMessage"]
    await admin.patch(f"/api/vms/{vm['id']}", json={"password": "secret"})

    # Otra máquina en la misma dirección (VM recreada o suplantada, otra clave
    # de host): no se conecta. Se cambia el puerto directo en la BD para no
    # pasar por la API, que olvida la clave al cambiar host/puerto.
    other, other_port = await _ssh_server(asyncssh.generate_private_key("ssh-ed25519"))
    try:
        async with AsyncSessionLocal() as db:
            stored = await db.get(TestVm, vm["id"])
            stored.port = other_port
            await db.commit()
        checked = (await admin.post(f"/api/vms/{vm['id']}/check")).json()["data"]
        assert checked["lastCheckOk"] is False and "no coincide" in checked["lastCheckMessage"]

        # Olvidar la clave del host la vuelve a aceptar (y guarda la nueva).
        resp = await admin.patch(f"/api/vms/{vm['id']}", json={"resetHostKey": True})
        assert resp.json()["data"]["hostKeyFingerprint"] is None
        checked = (await admin.post(f"/api/vms/{vm['id']}/check")).json()["data"]
        assert checked["lastCheckOk"] is True
    finally:
        other.close()


async def test_check_connection_with_key(admin, ssh_server):
    key = CLIENT_KEY.export_private_key("openssh").decode()
    vm = (await admin.post("/api/vms", json={"host": "127.0.0.1", "port": ssh_server, "username": "qa",
                                             "authType": "key", "privateKey": key})).json()["data"]
    checked = (await admin.post(f"/api/vms/{vm['id']}/check")).json()["data"]
    assert checked["lastCheckOk"] is True, checked["lastCheckMessage"]


async def test_check_connection_unreachable(admin):
    vm = (await admin.post("/api/vms", json={"host": "127.0.0.1", "port": 1, "username": "qa",
                                             "authType": "password", "password": "x"})).json()["data"]
    checked = (await admin.post(f"/api/vms/{vm['id']}/check")).json()["data"]
    assert checked["lastCheckOk"] is False and "No se pudo conectar a 127.0.0.1:1" in checked["lastCheckMessage"]


async def test_draft_connection_test_saves_nothing(admin, ssh_server):
    form = {"host": "127.0.0.1", "port": ssh_server, "username": "qa", "authType": "password"}
    # Alta: se prueba antes de guardar.
    result = (await admin.post("/api/vms/test", json={**form, "password": "secret"})).json()["data"]
    assert result["ok"] is True and result["message"] == "Linux 6.1.0 x86_64"
    assert result["hostKeyFingerprint"].startswith("SHA256:")
    result = (await admin.post("/api/vms/test", json={**form, "password": "mala"})).json()["data"]
    assert result["ok"] is False and "rechazó" in result["message"]
    assert (await admin.get("/api/vms")).json()["data"] == []

    # Edición: con la contraseña vacía usa la guardada; no toca la VM guardada.
    vm = (await admin.post("/api/vms", json={**form, "password": "secret"})).json()["data"]
    result = (await admin.post("/api/vms/test", params={"vmId": vm["id"]}, json={"name": "otro"})).json()["data"]
    assert result["ok"] is True
    result = (await admin.post("/api/vms/test", params={"vmId": vm["id"]}, json={"password": "mala"})).json()["data"]
    assert result["ok"] is False
    saved = (await admin.get("/api/vms")).json()["data"][0]
    assert saved["name"] == vm["name"] and saved["lastCheckOk"] is None and saved["hostKeyFingerprint"] is None

    # Datos inválidos: mismo error que al guardar.
    resp = await admin.post("/api/vms/test", json={**form})
    assert resp.status_code == 400
