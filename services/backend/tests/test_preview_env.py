import json
import os
import stat

import pytest

from src.config.database import AsyncSessionLocal
from src.models.app_setting import AppSetting
from src.services import preview_env_service, vault_client
from src.services.secret_box import decrypt_json
from tests.fake_vault import ROOT_TOKEN, FakeVault
from tests.test_previews import _git, _load, _setup, origin, vm_port  # noqa: F401 — fixtures

VAULT = "http://vault-test:8200"
MYSQL = {"host": "mysql-central", "port": 3306, "adminUser": "root", "adminPassword": "clave-admin-mysql"}


@pytest.fixture
def vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(vault_client, "transport", fake.transport())
    preview_env_service._token_cache.update(key=None)
    return fake


@pytest.fixture
def mysql(monkeypatch):
    """MySQL central falso: registra lo que se le pide."""
    calls = []
    monkeypatch.setattr(preview_env_service, "_mysql_ping", lambda platform: calls.append(("ping", platform["host"])))
    monkeypatch.setattr(preview_env_service, "_mysql_provision",
                        lambda platform, db, user, pw: calls.append(("provision", db, user, pw)))
    return calls


async def _connect(admin):
    resp = await admin.post("/api/preview-env/vault", json={"addr": VAULT, "token": ROOT_TOKEN})
    assert resp.status_code == 200, resp.text
    resp = await admin.put("/api/preview-env/mysql", json=MYSQL)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_connect_stores_only_optidevs_approle(admin, vault, mysql):
    status = await _connect(admin)
    assert status["connected"] and status["addr"] == VAULT
    assert status["mysql"] == {"host": "mysql-central", "port": 3306, "adminUser": "root"}
    assert "clave-admin-mysql" not in json.dumps(status)
    assert {"approle", "token"} <= vault.auth and vault.mounts["preview"] == "kv"
    assert vault.kv[("preview", "_platform/mysql")]["admin_password"] == "clave-admin-mysql"

    async with AsyncSessionLocal() as db:
        stored = decrypt_json((await db.get(AppSetting, "preview_vault")).value_enc)
    assert set(stored) == {"addr", "roleId", "secretId"} and ROOT_TOKEN not in json.dumps(stored)

    # Idempotente: reconectar no rompe nada (el mount ya existe).
    again = (await admin.post("/api/preview-env/vault", json={"addr": VAULT, "token": ROOT_TOKEN})).json()["data"]
    assert again["steps"] == ["AppRole optidev-preview lista"]

    # Contraseña vacía al editar el MySQL = se conserva.
    await admin.put("/api/preview-env/mysql", json={**MYSQL, "adminPassword": ""})
    assert vault.kv[("preview", "_platform/mysql")]["admin_password"] == "clave-admin-mysql"


async def test_bad_token_and_permissions(admin, vault):
    resp = await admin.post("/api/preview-env/vault", json={"addr": VAULT, "token": "token-malo"})
    assert resp.status_code == 502 and "403" in resp.json()["detail"]
    assert (await admin.get("/api/preview-env")).json()["data"]["connected"] is False

    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    assert (await admin.post("/api/preview-env/vault", json={"addr": VAULT, "token": ROOT_TOKEN},
                             headers=member)).status_code == 403
    assert (await admin.get("/api/preview-env", headers=member)).status_code == 403


async def test_prepare_user_environment(admin, vault, mysql):
    await _connect(admin)
    # Todo con la AppRole de optidev: si a su política le faltara un permiso, el Vault falso daría 403.
    resp = await admin.post("/api/preview-env/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"mount": "admin", "database": "db_admin", "dbUser": "u_admin",
                                   "dbHost": "mysql-central"}
    assert vault.mounts["admin"] == "kv"
    assert "admin/data/*" in vault.policies["svc-admin"]
    assert vault.roles["svc-admin"]["params"]["token_policies"] == ["svc-admin"]
    first = [c for c in mysql if c[0] == "provision"][-1]
    assert first[1:3] == ("db_admin", "u_admin") and len(first[3]) >= 24

    # Idempotente: la segunda vez reusa la contraseña guardada en el Vault.
    await admin.post("/api/preview-env/me")
    assert [c for c in mysql if c[0] == "provision"][-1] == first


async def test_needs_vault_and_mysql(admin, vault, mysql):
    resp = await admin.post("/api/preview-env/me")
    assert resp.status_code == 409 and "no está conectado" in resp.json()["detail"]
    await admin.post("/api/preview-env/vault", json={"addr": VAULT, "token": ROOT_TOKEN})
    resp = await admin.post("/api/preview-env/me")
    assert resp.status_code == 400 and "MySQL central" in resp.json()["detail"]


def test_compute_env_priority():
    variables = [
        {"name": "A", "defaultValue": "default"},
        {"name": "DB_NAME", "defaultValue": "otra"},
        {"name": "SECRET", "required": True},
        {"name": "FALTA", "required": True},
        {"name": "OPCIONAL"},
    ]
    env, origin, missing = preview_env_service.compute_env(
        variables, {"SECRET": "s3", "A": ""}, {"DB_NAME": "db_admin", "NO_DECLARADA": "x"})
    assert env == {"A": "default", "DB_NAME": "db_admin", "SECRET": "s3"}
    assert origin == {"vault": 1, "usuario": 1, "default": 1}
    assert missing == ["FALTA"]
    assert preview_env_service.render_env(env) == "A=default\nDB_NAME=db_admin\nSECRET=s3\n"
    with pytest.raises(vault_client.VaultError):
        preview_env_service.render_env({"X": "a\nb"})


def _add_requirements(origin):  # noqa: F811
    service = origin / "services" / "api"
    service.mkdir(parents=True)
    (service / "requirements.json").write_text(json.dumps({
        "service": "api",
        "environment": {"envFile": "services/api/.env", "variables": [
            {"name": "API_PORT", "defaultValue": "4000"},
            {"name": "DB_NAME", "required": True},
            {"name": "DB_PASSWORD", "required": True, "looksSensitive": True},
            {"name": "VAULT_MOUNT_POINT", "required": True},
            {"name": "VAULT_SECRET_ID", "required": True},
            {"name": "ARI_APP_NAME", "required": True},
            {"name": "API_KEY", "required": True, "looksSensitive": True},
            {"name": "SIN_VALOR", "required": True},
        ]},
    }))
    (origin / ".gitignore").write_text(".env-local\n.env\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-qm", "requirements")


async def test_load_writes_env_files(admin, vm_port, origin, vault, mysql, tmp_path):  # noqa: F811
    _add_requirements(origin)
    _, vm = await _setup(admin, vm_port, recipe="echo receta; cat services/api/.env | wc -l\n")
    await _connect(admin)
    vault.kv[("preview", "demo/api")] = {"API_KEY": "clave-api-secreta"}

    load = await _load(admin, vm)
    assert load["status"] == "success", load["log"]
    env_file = tmp_path / "vm" / "demo" / "services" / "api" / ".env"
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert values["API_PORT"] == "4000" and values["DB_NAME"] == "db_admin" and values["VAULT_MOUNT_POINT"] == "admin"
    assert values["API_KEY"] == "clave-api-secreta" and len(values["VAULT_SECRET_ID"]) > 10
    assert values["ARI_APP_NAME"] == "routing-app-admin"
    assert "SIN_VALOR" not in values
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600

    log = load["log"]
    assert "api: 7 variables (1 del Vault, 5 de tu ambiente, 1 por defecto)" in log
    assert "faltan obligatorias: SIN_VALOR" in log and "preview/demo/api" in log
    # Ningún valor sensible en el log.
    for secret in ("clave-api-secreta", values["DB_PASSWORD"], values["VAULT_SECRET_ID"]):
        assert secret not in log
    # El secret_id de la carga es de svc-admin (lo que leen los servicios).
    assert values["VAULT_SECRET_ID"] in vault.roles["svc-admin"]["secret_ids"]


async def test_load_without_vault_still_runs_recipe(admin, vm_port, origin, vault):  # noqa: F811
    _add_requirements(origin)
    _, vm = await _setup(admin, vm_port)
    load = await _load(admin, vm)
    assert load["status"] == "success", load["log"]
    assert "no está conectado" in load["log"] and "los .env no se arman" in load["log"]


async def test_fake_vault_enforces_optidevs_policy(admin, vault, mysql, monkeypatch):
    # Sin permiso para crear mounts, preparar el ambiente tiene que fallar con 403
    # (prueba que el Vault falso aplica las políticas, no que deja pasar todo).
    weaker = "\n".join(line for line in preview_env_service.OPTIDEV_POLICY.splitlines()
                       if not line.startswith('path "sys/mounts/*"'))
    assert weaker != preview_env_service.OPTIDEV_POLICY
    monkeypatch.setattr(preview_env_service, "OPTIDEV_POLICY", weaker)
    await _connect(admin)
    resp = await admin.post("/api/preview-env/me")
    assert resp.status_code == 502 and "permiso denegado" in resp.json()["detail"]
