import json
from datetime import datetime, timedelta, timezone

import pytest

from src.config.settings import settings


@pytest.fixture
def update_dirs(tmp_path, monkeypatch):
    request_dir, state_dir = tmp_path / "data" / "update", tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(settings, "UPDATE_REQUEST_DIR", str(request_dir))
    monkeypatch.setattr(settings, "UPDATE_STATE_DIR", str(state_dir))
    return request_dir, state_dir


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_status(state_dir, state, updated=None):
    (state_dir / "status.json").write_text(json.dumps({
        "state": state, "step": "migrations", "message": "x",
        "updatedAt": _iso(updated or datetime.now(timezone.utc)),
    }))


async def _member(admin):
    resp = await admin.post("/api/users", json={
        "username": "pepe", "email": "pepe@x.com", "name": "Pepe", "password": "pepe-pass-123", "role": "member",
    })
    assert resp.status_code == 201, resp.text
    login = await admin.post("/api/auth/login", json={"username": "pepe", "password": "pepe-pass-123"})
    return {"Authorization": f"Bearer {login.json()['data']['token']}"}


async def test_update_requires_admin(admin, update_dirs):
    headers = await _member(admin)
    assert (await admin.get("/api/system/update", headers=headers)).status_code == 403
    assert (await admin.post("/api/system/update", headers=headers)).status_code == 403
    assert not (update_dirs[0] / "request").exists()


async def test_update_requires_auth(client, update_dirs):
    assert (await client.post("/api/system/update")).status_code == 401


async def test_update_not_installed(admin, update_dirs, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPDATE_STATE_DIR", str(tmp_path / "missing"))
    data = (await admin.get("/api/system/update")).json()["data"]
    assert data["available"] is False
    assert (await admin.post("/api/system/update")).status_code == 503
    assert not (update_dirs[0] / "request").exists()


async def test_request_update(admin, update_dirs):
    request_dir, state_dir = update_dirs
    resp = await admin.post("/api/system/update", json={"branch": "evil", "cmd": "rm -rf /"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"]["pending"] is True
    # El archivo de pedido no lleva datos de la request (el script lo ignora igual).
    assert (request_dir / "request").read_bytes() == b""
    meta = json.loads((request_dir / "last_request.json").read_text())
    assert meta["requestedBy"] == "admin"

    # Un segundo pedido con el primero pendiente se rechaza.
    assert (await admin.post("/api/system/update")).status_code == 409

    # El script consume el pedido y deja estado y log.
    (request_dir / "request").unlink()
    _write_status(state_dir, "running")
    (state_dir / "update.log").write_text("[00:00:00] $ git fetch\n")
    data = (await admin.get("/api/system/update")).json()["data"]
    assert data["pending"] is False and data["running"] is True
    assert data["status"]["step"] == "migrations"
    assert "git fetch" in data["log"]
    assert data["lastRequest"]["requestedBy"] == "admin"
    assert (await admin.post("/api/system/update")).status_code == 409

    _write_status(state_dir, "success")
    assert (await admin.post("/api/system/update")).status_code == 202


async def test_stale_running_does_not_block(admin, update_dirs):
    _, state_dir = update_dirs
    _write_status(state_dir, "running", datetime.now(timezone.utc) - timedelta(hours=2))
    data = (await admin.get("/api/system/update")).json()["data"]
    assert data["running"] is False
    assert data["status"]["state"] == "failed"
    assert (await admin.post("/api/system/update")).status_code == 202


async def test_log_tail_is_bounded(admin, update_dirs):
    _, state_dir = update_dirs
    (state_dir / "update.log").write_text("".join(f"linea {i}\n" for i in range(20000)))
    log = (await admin.get("/api/system/update")).json()["data"]["log"]
    assert len(log) <= 32 * 1024
    assert log.startswith("linea ") and log.endswith("linea 19999\n")
