from datetime import datetime, timezone

from src.config.database import AsyncSessionLocal
from src.models.user import User
from src.services import github_service, secret_box


def _status(readiness):
    return {c["key"]: c["ok"] for c in readiness["checks"]}


async def _fake_login(token):
    if token != "ghp_bueno":
        raise github_service.ServiceError("GitHub rechazó el token (inválido o vencido)")
    return "octocat"


async def test_github_token(admin, monkeypatch):
    monkeypatch.setattr(github_service, "fetch_login", _fake_login)
    bad = await admin.put("/api/auth/me/github-token", json={"token": "malo"})
    assert bad.status_code == 400

    resp = await admin.put("/api/auth/me/github-token", json={"token": " ghp_bueno "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["githubLogin"] == "octocat"
    assert "ghp_bueno" not in resp.text
    async with AsyncSessionLocal() as db:
        user = await db.get(User, 1)
        assert secret_box.decrypt_json(user.github_token_enc) == {"token": "ghp_bueno"}

    resp = await admin.delete("/api/auth/me/github-token")
    assert resp.json()["data"]["githubLogin"] is None


async def test_readiness(admin, monkeypatch):
    data = (await admin.get("/api/auth/me/readiness")).json()["data"]
    assert data["ready"] is False
    assert _status(data) == {
        "mfa": False, "vm": False, "claudeCode": False, "github": False, "routine": False,
    }

    # VM: alcanza con tenerla cargada (no se prueba la conexión).
    resp = await admin.post("/api/vms", json={"name": "QA", "host": "10.0.0.5", "username": "qa",
                                              "authType": "password", "password": "secret"})
    assert resp.status_code == 201, resp.text

    # Claude Code: el token tiene que haberse usado, crearlo no alcanza.
    raw = (await admin.post("/api/tokens", json={"name": "cc"})).json()["data"]["token"]
    assert _status((await admin.get("/api/auth/me/readiness")).json()["data"])["claudeCode"] is False
    assert (await admin.get("/api/board", headers={"Authorization": f"Bearer {raw}"})).status_code == 200

    monkeypatch.setattr(github_service, "fetch_login", _fake_login)
    await admin.put("/api/auth/me/github-token", json={"token": "ghp_bueno"})

    async with AsyncSessionLocal() as db:
        user = await db.get(User, 1)
        user.mfa_enabled_at = datetime.now(timezone.utc)
        user.routine_last_run_at = datetime.now(timezone.utc)
        await db.commit()

    data = (await admin.get("/api/auth/me/readiness")).json()["data"]
    assert data["ready"] is True, data

    users = (await admin.get("/api/users")).json()["data"]
    assert users[0]["readiness"]["ready"] is True
