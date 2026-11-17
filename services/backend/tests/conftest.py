import asyncio
import os
import tempfile

# Settings se leen al importar src.*: la BD de test y el secreto van antes.
_tmpdir = tempfile.mkdtemp(prefix="optidev-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmpdir}/test.db"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 32
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "admin-pass-123"
os.environ["UPLOAD_DIR"] = f"{_tmpdir}/uploads"
# Las "VMs" de los tests son servidores SSH en 127.0.0.1 (ver test_previews).
os.environ["PREVIEW_ALLOW_SELF"] = "true"
# Los tests ingresan solo con contraseña; test_mfa.py activa el MFA obligatorio.
os.environ["MFA_REQUIRED"] = "false"

import httpx
import pytest

from src.app import app
from src.config.database import AsyncSessionLocal, Base, engine
from src.mcp_server.server import mcp
from src.models.user import User
from src.services.bootstrap import bootstrap
from src.services.secret_box import encrypt_json
import src.models  # noqa: F401

USER_TOKEN = "ghp_del_usuario"


@pytest.fixture(scope="session")
async def mcp_running():
    # httpx no ejecuta el lifespan: el session manager del MCP se levanta a mano,
    # en una task propia porque anyio exige entrar y salir desde la misma task.
    started, stop = asyncio.Event(), asyncio.Event()

    async def run():
        async with mcp.session_manager.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(run())
    await started.wait()
    yield
    stop.set()
    await task


@pytest.fixture(autouse=True)
def _no_review_on_create(monkeypatch):
    """Cargar un repo no revisa su salud en GitHub, salvo en los tests de salud de repos que lo piden."""
    from src.services import repo_health_service

    async def skip(db, repo):
        return None

    monkeypatch.setattr(repo_health_service, "check_new_repository", skip)


@pytest.fixture
async def client(mcp_running):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
async def admin(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['data']['token']}"
    return client


@pytest.fixture
async def github_user(admin):
    """El admin con token personal de GitHub cargado (para las acciones que escriben en GitHub)."""
    async with AsyncSessionLocal() as db:
        user = await db.get(User, 1)
        user.github_token_enc = encrypt_json({"token": USER_TOKEN})
        await db.commit()
    return admin
