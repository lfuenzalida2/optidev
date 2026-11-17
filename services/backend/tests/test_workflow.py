from types import SimpleNamespace

import pytest

from src.config.settings import settings
from src.services import workflow_service
from tests.conftest import USER_TOKEN

SHA = "a" * 40
FILES = {"README.md": "# Agentes\n", "repo-docs/helios/README.md": "Helios\nviejo\n"}


@pytest.fixture
def github(monkeypatch):
    """GitHub falso: un commit con FILES. Registra las escrituras."""
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    workflow_service._head_cache.update(at=0.0, key=None, sha=None)
    workflow_service._tree_cache.update(key=None, data=None)
    calls = []

    async def fake_rest(method, path, json=None, raw=False, token=None):
        calls.append((method, path, json))
        # Leer va con el token de la app; escribir, con el del usuario.
        assert (token == USER_TOKEN) == (method != "GET"), f"{method} {path} con token {token}"
        base = "/repos/org/optidev-agents"
        if path == f"{base}/git/ref/heads/master":
            return {"object": {"sha": SHA}}
        if path == f"{base}/git/trees/{SHA}?recursive=1":
            return {"truncated": False, "tree": [
                {"path": "README.md", "type": "blob", "size": 10, "mode": "100644"},
                {"path": "repo-docs", "type": "tree", "mode": "040000"},
                {"path": "repo-docs/helios", "type": "tree", "mode": "040000"},
                {"path": "repo-docs/helios/README.md", "type": "blob", "size": 13, "mode": "100644"},
                {"path": "logo.png", "type": "blob", "size": 4, "mode": "100644"},
            ]}
        if path.startswith(f"{base}/contents/"):
            name = path.removeprefix(f"{base}/contents/").split("?")[0]
            return b"\x89PNG" if name == "logo.png" else FILES[name].encode()
        if path == f"{base}/git/commits/{SHA}":
            return {"tree": {"sha": "t" * 40}}
        if path == f"{base}/git/trees":
            return {"sha": "n" * 40}
        if path == f"{base}/git/commits":
            return {"sha": "c" * 40}
        if path == f"{base}/git/refs":
            return {}
        if path == f"{base}/pulls":
            return {"html_url": "https://github.com/org/optidev-agents/pull/9", "number": 9}
        raise AssertionError(f"llamada inesperada: {method} {path}")

    monkeypatch.setattr(workflow_service, "_rest", fake_rest)
    return calls


@pytest.fixture
async def agents_repo(github_user):
    admin = github_user
    resp = await admin.post("/api/repositories", json={
        "key": "agents", "name": "Optidev Agents", "defaultBranch": "master",
        "githubUrl": "https://github.com/org/optidev-agents",
    })
    assert resp.status_code == 201, resp.text
    return admin


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _message(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


async def test_requires_config(agents_repo, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    resp = await agents_repo.get("/api/workflow/tree")
    assert resp.status_code == 503 and "GITHUB_TOKEN" in resp.json()["detail"]

    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    resp = await agents_repo.post("/api/workflow/chat", json={"messages": [{"role": "user", "content": "hola"}]})
    assert resp.status_code == 503 and "ANTHROPIC_API_KEY" in resp.json()["detail"]


async def test_requires_agents_repo(admin, github):
    resp = await admin.get("/api/workflow/tree")
    assert resp.status_code == 503
    assert "'agents'" in resp.json()["detail"]


async def test_tree_and_file(agents_repo, github):
    resp = await agents_repo.get("/api/workflow/tree")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["sha"] == SHA
    assert data["repository"]["branch"] == "master"
    assert {"path": "repo-docs", "type": "dir", "size": None} in data["entries"]
    assert {"path": "README.md", "type": "file", "size": 10} in data["entries"]

    resp = await agents_repo.get("/api/workflow/file", params={"path": "repo-docs/helios/README.md", "ref": SHA})
    assert resp.status_code == 200, resp.text
    file = resp.json()["data"]
    assert file["content"] == "Helios\nviejo\n" and not file["binary"]
    assert file["githubUrl"].endswith(f"/blob/{SHA}/repo-docs/helios/README.md")

    assert (await agents_repo.get("/api/workflow/file", params={"path": "logo.png"})).json()["data"]["binary"]
    assert (await agents_repo.get("/api/workflow/file", params={"path": "nada.md"})).status_code == 404
    assert (await agents_repo.get("/api/workflow/file", params={"path": "README.md", "ref": "main"})).status_code == 422


async def test_chat_reads_and_proposes(agents_repo, github, monkeypatch):
    requests = []
    replies = [
        _message("tool_use", _text("Reviso el archivo."), _tool("t1", "read_file", {"path": "repo-docs/helios/README.md"}),
                 _tool("t2", "read_file", {"path": "no-existe.md"})),
        _message("tool_use", _tool("t3", "propose_changes", {
            "title": "Actualizar doc de Helios", "description": "Corrige la doc.",
            "files": [{"path": "repo-docs/helios/README.md", "content": "Helios\nnuevo\n"},
                      {"path": "repo-docs/helios/nuevo.md", "content": "hola\n"}],
        })),
        _message("end_turn", _text("Te dejé la propuesta para aprobar.")),
    ]

    async def fake_claude(**params):
        # Copia: `messages` es la misma lista que el servicio sigue extendiendo.
        requests.append({**params, "messages": list(params["messages"])})
        return replies[len(requests) - 1]

    monkeypatch.setattr(workflow_service, "_claude", fake_claude)
    resp = await agents_repo.post("/api/workflow/chat", json={"messages": [
        {"role": "user", "content": "¿Qué dice la doc de helios?"},
        {"role": "assistant", "content": "Dice que es Helios."},
        {"role": "user", "content": "Cambia 'viejo' por 'nuevo'."},
    ]})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["reply"] == "Te dejé la propuesta para aprobar."
    assert data["sha"] == SHA

    # La lista de archivos va en el system prompt; el modelo por defecto es Sonnet.
    first = requests[0]
    assert first["model"] == "claude-sonnet-5-5"
    assert "repo-docs/helios/README.md" in first["system"]
    # Los resultados de las tools vuelven en un único mensaje, con el error marcado.
    results = requests[1]["messages"][-1]["content"]
    assert results[0] == {"type": "tool_result", "tool_use_id": "t1", "content": "Helios\nviejo\n"}
    assert results[1]["is_error"] and "no-existe.md" in results[1]["content"]

    proposal = data["proposal"]
    assert proposal["title"] == "Actualizar doc de Helios" and proposal["baseSha"] == SHA
    edit, new = proposal["files"]
    assert not edit["isNew"] and "-viejo" in edit["diff"] and "+nuevo" in edit["diff"]
    assert new["isNew"] and "/dev/null" in new["diff"]
    # El archivo ya leído no se vuelve a pedir a GitHub para armar el diff.
    assert sum(1 for _, path, _ in github if path.endswith("contents/repo-docs/helios/README.md?ref=" + SHA)) == 1
    # Proponer no escribe nada en GitHub.
    assert all(method == "GET" for method, _, _ in github)


async def test_chat_rejects_invalid_proposals(agents_repo, github, monkeypatch):
    requests = []
    replies = [
        _message("tool_use", _tool("t1", "propose_changes", {
            "title": "CI", "description": "", "files": [{"path": ".github/workflows/x.yml", "content": "on: push"}],
        })),
        _message("end_turn", _text("No puedo tocar .github.")),
    ]

    async def fake_claude(**params):
        # Copia: `messages` es la misma lista que el servicio sigue extendiendo.
        requests.append({**params, "messages": list(params["messages"])})
        return replies[len(requests) - 1]

    monkeypatch.setattr(workflow_service, "_claude", fake_claude)
    resp = await agents_repo.post("/api/workflow/chat", json={"messages": [{"role": "user", "content": "agrega CI"}]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["proposal"] is None
    result = requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] and ".github/" in result["content"]


async def test_chat_history_must_end_with_user(agents_repo, github):
    resp = await agents_repo.post("/api/workflow/chat", json={"messages": [
        {"role": "user", "content": "hola"}, {"role": "assistant", "content": "hola"},
    ]})
    assert resp.status_code == 400


async def test_create_pull_request(agents_repo, github):
    resp = await agents_repo.post("/api/workflow/pull-requests", json={
        "title": "Actualizar doc de Helios", "description": "Corrige la doc.", "baseSha": SHA,
        "files": [{"path": "repo-docs/helios/README.md", "content": "Helios\nnuevo\n"},
                  {"path": "README.md", "content": None}],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["number"] == 9
    assert data["branch"].startswith("workflow/") and data["branch"].endswith("-actualizar-doc-de-helios")

    writes = {path.rsplit("/", 2)[-2] + "/" + path.rsplit("/", 1)[-1]: body
              for method, path, body in github if method == "POST"}
    tree = writes["git/trees"]
    assert tree["base_tree"] == "t" * 40
    assert tree["tree"] == [
        {"path": "repo-docs/helios/README.md", "mode": "100644", "type": "blob", "content": "Helios\nnuevo\n"},
        {"path": "README.md", "mode": "100644", "type": "blob", "sha": None},
    ]
    # El commit va sobre el commit que vio Claude, no sobre la punta actual de la rama.
    assert writes["git/commits"]["parents"] == [SHA]
    assert writes["git/refs"]["ref"] == f"refs/heads/{data['branch']}"
    pr = writes["optidev-agents/pulls"]
    assert pr["base"] == "master" and pr["head"] == data["branch"]
    assert "aprobado por" in pr["body"]


async def test_create_pull_request_rejects_github_dir(agents_repo, github):
    resp = await agents_repo.post("/api/workflow/pull-requests", json={
        "title": "CI", "baseSha": SHA, "files": [{"path": ".github/workflows/x.yml", "content": "on: push"}],
    })
    assert resp.status_code == 400
    assert not any(method == "POST" for method, _, _ in github)
