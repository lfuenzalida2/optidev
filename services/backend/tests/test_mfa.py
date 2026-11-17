from datetime import datetime, timedelta, timezone

import pyotp
import pytest

from src.config.settings import settings
from src.services import mfa_service

ADMIN = {"username": "admin", "password": "admin-pass-123"}


@pytest.fixture
def mfa_required(monkeypatch):
    monkeypatch.setattr(settings, "MFA_REQUIRED", True)


def _code(secret, offset=0):
    totp = pyotp.TOTP(secret)
    return totp.generate_otp(totp.timecode(datetime.now(timezone.utc)) + offset)


async def _login(client, creds=ADMIN):
    resp = await client.post("/api/auth/login", json=creds)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _enroll(client, creds=ADMIN):
    """Registra el MFA y devuelve (secreto, token de acceso)."""
    mfa = (await _login(client, creds))["mfa"]
    assert mfa["step"] == "setup"
    setup = (await client.post("/api/auth/mfa/setup", json={"mfaToken": mfa["token"]})).json()["data"]
    assert setup["otpauthUri"].startswith("otpauth://totp/Optidev:")
    resp = await client.post("/api/auth/mfa/verify", json={"mfaToken": mfa["token"], "code": _code(setup["secret"])})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user"]["mfaEnabled"] is True
    return setup["secret"], data["token"]


async def test_first_login_requires_setup(client, mfa_required):
    data = await _login(client)
    assert "token" not in data and data["mfa"]["step"] == "setup"
    # El token de MFA no sirve como token de acceso.
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['mfa']['token']}"})
    assert resp.status_code == 401

    secret, token = await _enroll(client)
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["data"]["mfaEnabled"] is True


async def test_login_with_code_and_no_reuse(client, mfa_required):
    secret, _ = await _enroll(client)
    # El código del registro ya se usó; el del paso siguiente sí sirve.
    mfa = (await _login(client))["mfa"]
    assert mfa["step"] == "verify"
    reused = await client.post("/api/auth/mfa/verify", json={"mfaToken": mfa["token"], "code": _code(secret)})
    assert reused.status_code == 400
    ok = await client.post("/api/auth/mfa/verify", json={"mfaToken": mfa["token"], "code": _code(secret, 1)})
    assert ok.status_code == 200, ok.text
    # Ya registrado: no se puede pisar el secreto con el token de MFA.
    assert (await client.post("/api/auth/mfa/setup", json={"mfaToken": mfa["token"]})).status_code == 409


async def test_existing_mfa_applies_even_if_not_required(client, monkeypatch, mfa_required):
    await _enroll(client)
    monkeypatch.setattr(settings, "MFA_REQUIRED", False)
    assert (await _login(client))["mfa"]["step"] == "verify"


async def test_lockout_after_failed_codes(client, mfa_required):
    secret, _ = await _enroll(client)
    mfa = (await _login(client))["mfa"]
    for _ in range(mfa_service.MAX_ATTEMPTS):
        resp = await client.post("/api/auth/mfa/verify", json={"mfaToken": mfa["token"], "code": "000000"})
        assert resp.status_code == 400
    # Bloqueado: ni el código correcto pasa.
    resp = await client.post("/api/auth/mfa/verify", json={"mfaToken": mfa["token"], "code": _code(secret, 1)})
    assert resp.status_code == 429


async def test_invalid_mfa_token(client, mfa_required):
    resp = await client.post("/api/auth/mfa/verify", json={"mfaToken": "x.y.z", "code": "123456"})
    assert resp.status_code == 401
    expired = mfa_service.jwt.encode(
        {"userId": 1, "typ": "mfa", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
        settings.JWT_SECRET, algorithm="HS256",
    )
    assert (await client.post("/api/auth/mfa/setup", json={"mfaToken": expired})).status_code == 401


async def test_admin_resets_mfa(client, mfa_required):
    _, admin_token = await _enroll(client)
    client.headers["Authorization"] = f"Bearer {admin_token}"
    member = {"username": "ana", "password": "pass-ana-123"}
    created = await client.post("/api/users", json={**member, "email": "ana@x.cl", "name": "Ana", "role": "member"})
    ana_id = created.json()["data"]["id"]
    await _enroll(client, member)
    users = (await client.get("/api/users")).json()["data"]
    assert next(u for u in users if u["id"] == ana_id)["mfaEnabled"] is True

    resp = await client.post(f"/api/users/{ana_id}/mfa/reset")
    assert resp.status_code == 200 and resp.json()["data"]["mfaEnabled"] is False
    # Vuelve a registrarlo en el próximo ingreso.
    assert (await _login(client, member))["mfa"]["step"] == "setup"

    # Solo un admin puede reiniciarlo.
    _, ana_token = await _enroll(client, member)
    resp = await client.post(f"/api/users/{ana_id}/mfa/reset", headers={"Authorization": f"Bearer {ana_token}"})
    assert resp.status_code == 403
