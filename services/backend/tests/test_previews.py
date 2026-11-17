import asyncio
import subprocess

import asyncssh
import pytest

from src.config.database import AsyncSessionLocal
from src.config.settings import settings
from src.models.preview_load import PreviewLoad
from src.services import preview_service

RECIPE = 'echo "rama=$PREVIEW_BRANCH host=$PREVIEW_HOST"\ncat app.txt\n'


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return password == "secret"


async def _run_locally(process):
    """La "VM" es esta misma máquina: ejecuta el comando con el shell local."""
    proc = await asyncio.create_subprocess_shell(process.command, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
    async for line in proc.stdout:
        process.stdout.write(line.decode())
    process.exit(await proc.wait())


@pytest.fixture
async def vm_port():
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
        process_factory=_run_locally, sftp_factory=True,
    )
    yield server.sockets[0].getsockname()[1]
    server.close()


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


@pytest.fixture
def origin(tmp_path, monkeypatch):
    """Repo que hace de GitHub, con la rama feat/x."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "staging")
    (repo / ".gitignore").write_text(".env-local\n")
    (repo / "app.txt").write_text("version 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "inicial")
    _git(repo, "checkout", "-qb", "feat/x")
    monkeypatch.setattr(preview_service, "_remote_url", lambda _repo: str(repo))
    monkeypatch.setattr(settings, "PREVIEW_MIRROR_DIR", str(tmp_path / "mirrors"))
    monkeypatch.setattr(settings, "PREVIEW_REMOTE_DIR", str(tmp_path / "vm"))
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_no_tiene_que_aparecer")
    return repo


async def _setup(admin, vm_port, recipe=RECIPE):
    await admin.post("/api/repositories", json={"key": "demo", "name": "Demo",
                                                "githubUrl": "https://github.com/org/demo"})
    repo = next(r for r in (await admin.get("/api/repositories")).json()["data"] if r["key"] == "demo")
    resp = await admin.put(f"/api/repositories/{repo['id']}/preview",
                           json={"script": recipe, "url": "http://{host}:9000"})
    assert resp.status_code == 200, resp.text
    vm = (await admin.post("/api/vms", json={"host": "127.0.0.1", "port": vm_port, "username": "qa",
                                             "authType": "password", "password": "secret"})).json()["data"]
    return repo, vm


async def _load(admin, vm, branch="feat/x"):
    resp = await admin.post("/api/previews", json={"repository": "demo", "branch": branch, "vmId": vm["id"],
                                                   "cardId": 7, "prUrl": "https://github.com/org/demo/pull/3"})
    assert resp.status_code == 202, resp.text
    load_id = resp.json()["data"]["id"]
    for _ in range(300):
        load = (await admin.get(f"/api/previews/{load_id}")).json()["data"]
        if load["status"] != "running":
            return load
        await asyncio.sleep(0.05)
    raise AssertionError(f"La carga no terminó: {load['log']}")


async def test_load_branch_into_vm(admin, vm_port, origin, tmp_path):
    _, vm = await _setup(admin, vm_port)
    load = await _load(admin, vm)
    assert load["status"] == "success", load["log"]
    assert "rama=feat/x host=127.0.0.1" in load["log"] and "version 1" in load["log"]
    assert load["url"] == "http://127.0.0.1:9000"
    assert load["sha"] and load["sha"] in load["log"]
    assert "ghp_no_tiene_que_aparecer" not in load["log"]
    workdir = tmp_path / "vm" / "demo"
    assert (workdir / "app.txt").read_text() == "version 1\n"

    # Nueva versión de la rama: se actualiza; lo ignorado (.env-local) se conserva.
    (workdir / ".env-local").write_text("SECRETO=1\n")
    (origin / "app.txt").write_text("version 2\n")
    _git(origin, "commit", "-qam", "cambio")
    load = await _load(admin, vm)
    assert load["status"] == "success", load["log"]
    assert (workdir / "app.txt").read_text() == "version 2\n"
    assert (workdir / ".env-local").exists()
    assert not list((tmp_path).glob("vm/demo/*.bundle"))

    current = (await admin.get("/api/previews")).json()["data"]
    assert len(current) == 1 and current[0]["id"] == load["id"] and current[0]["cardId"] == 7


async def test_failures(admin, vm_port, origin):
    repo, vm = await _setup(admin, vm_port, recipe="echo arrancando\nexit 3\n")
    load = await _load(admin, vm)
    assert load["status"] == "failed" and load["step"] == "recipe"
    assert "arrancando" in load["log"] and "código 3" in load["log"]

    load = await _load(admin, vm, branch="no-existe")
    assert load["status"] == "failed" and load["step"] == "fetch"

    bad = await admin.post("/api/previews", json={"repository": "demo", "branch": "x; rm -rf /", "vmId": vm["id"]})
    assert bad.status_code == 400

    # Sin receta no se puede cargar.
    await admin.put(f"/api/repositories/{repo['id']}/preview", json={"script": "  "})
    resp = await admin.post("/api/previews", json={"repository": "demo", "branch": "feat/x", "vmId": vm["id"]})
    assert resp.status_code == 400 and "receta" in resp.json()["detail"]


async def test_one_load_at_a_time_per_vm_and_repo(admin, vm_port, origin):
    _, vm = await _setup(admin, vm_port, recipe="sleep 1\n")
    body = {"repository": "demo", "branch": "feat/x", "vmId": vm["id"]}
    first = await admin.post("/api/previews", json=body)
    second = await admin.post("/api/previews", json=body)
    assert first.status_code == 202 and second.status_code == 409
    while (await admin.get(f"/api/previews/{first.json()['data']['id']}")).json()["data"]["status"] == "running":
        await asyncio.sleep(0.05)


async def test_permissions(admin, vm_port, origin):
    repo, vm = await _setup(admin, vm_port)
    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}

    # La receta corre como root en las VMs de todos: solo la editan admins.
    resp = await admin.put(f"/api/repositories/{repo['id']}/preview", json={"script": "id"}, headers=member)
    assert resp.status_code == 403
    # No se puede cargar en la VM de otro ni ver sus cargas.
    resp = await admin.post("/api/previews", json={"repository": "demo", "branch": "feat/x", "vmId": vm["id"]},
                            headers=member)
    assert resp.status_code == 404
    load = await _load(admin, vm)
    assert (await admin.get(f"/api/previews/{load['id']}", headers=member)).status_code == 404
    assert (await admin.get("/api/previews", headers=member)).json()["data"] == []


async def test_interrupted_loads_fail_on_startup(admin, vm_port, origin):
    _, vm = await _setup(admin, vm_port)
    async with AsyncSessionLocal() as db:
        db.add(PreviewLoad(user_id=1, vm_id=vm["id"], repository_id=1, branch="feat/x", status="running", log=""))
        await db.commit()
    await preview_service.fail_interrupted()
    current = (await admin.get("/api/previews")).json()["data"]
    assert current[0]["status"] == "failed"
