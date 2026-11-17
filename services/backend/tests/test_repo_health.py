import asyncio
import base64
import json

import pytest

from src.config.settings import settings
from src.services import github_service, repo_health_service
from src.services.errors import ServiceError

PROTECTED = {"required_pull_request_reviews": {"required_approving_review_count": 1}}
NOT_PROTECTED = {"message": "Branch not protected"}
# Los labels del flujo de PRs con los colores de mms-backend.
STANDARD = [{"name": "Aprobado", "color": "1d76db"}, {"name": "Revisado", "color": "1caf55"},
            {"name": "Pendiente", "color": "7b1e66"}, {"name": "Waiting", "color": "f9d0c4"},
            {"name": "Breaking Change", "color": "b60205"}]
WORKFLOWS = repo_health_service.STANDARD_WORKFLOWS


def _wf(*names, state="active"):
    return [{"id": i, "state": state, "path": f".github/workflows/{n}.yml", "name": n} for i, n in enumerate(names, 1)]


DOC_SHA = "abc1234"


def _doc(name, sha=DOC_SHA, branch="staging"):
    """README de `repo-docs/<repo>` con su front-matter."""
    return f"---\nrepo: {name}\ndocumentado_en_rama: {branch}\ndocumentado_en_commit: {sha}\n---\n\n# {name}\n"


def _good_files(name):
    """Los archivos que cumplen el estándar de agentes."""
    config = {"extraKnownMarketplaces": {"optidev-tools": {"source": {"source": "github"}}},
              "enabledPlugins": {"task-workflow-tools@optidev-tools": True}}
    return {"CLAUDE.md": f"# {name}\n\nDocumentación: repo-docs/{name}\n\n## Verificar\n\npytest -q\n",
            ".claude/settings.json": json.dumps(config),
            ".github/workflows/ci.yml": "name: CI\njobs:\n  ci:\n    steps:\n      - run: pytest -q\n      - run: ruff check .\n"}


class FakeGithub:
    """API REST de GitHub en memoria, por repo `org/nombre`."""

    def __init__(self):
        self.repos = {}
        self.calls = []
        self.writes = []
        self.tokens = set()
        self.forbid = set()  # "METHOD resto-del-path" que GitHub rechaza con 403

    def add(self, name, private=True, branches=("master", "staging"), protection=PROTECTED, rules=(),
            labels=None, workflows=None, files=None, ahead=0, staging_protection="same"):
        self.repos[f"org/{name}"] = {
            "private": private, "branches": branches, "protection": protection, "rules": list(rules),
            "labels": STANDARD if labels is None else labels,
            "workflows": _wf(*WORKFLOWS) if workflows is None else list(workflows),
            "files": _good_files(name) if files is None else dict(files),
            "commits": {DOC_SHA: DOC_SHA + "0" * 33},  # SHA corto → completo de los commits que existen
            "ahead": ahead,  # commits que lleva cualquier rama sobre el commit documentado
            "staging_protection": protection if staging_protection == "same" else staging_protection,
        }

    async def rest(self, method, path, json=None, allow=()):
        self.calls.append(path)
        self.tokens.add(github_service._token_override.get())
        parts = path.split("?")[0].split("/")  # ['', 'repos', org, name, ...]
        repo = self.repos.get(f"{parts[2]}/{parts[3]}")
        if repo is None:
            return self._answer(404, {"message": "Not Found"}, allow)
        rest = "/".join(parts[4:])
        if method != "GET":
            self.writes.append((method, "/".join(parts[3:]), json))
            return self._write(repo, method, rest, json, allow)
        if rest == "":
            return 200, {"private": repo["private"]}
        if rest.startswith("branches/") and rest.endswith("/protection"):
            branch = rest.split("/")[1]
            protection = repo["staging_protection"] if branch == "staging" else repo["protection"]
            if branch not in repo["branches"] or protection is None:
                return self._answer(404, NOT_PROTECTED, allow)
            if protection == "forbidden":
                return self._answer(403, {"message": "Resource not accessible"}, allow)
            return 200, protection
        if rest.startswith("git/ref/heads/"):
            found = rest.removeprefix("git/ref/heads/") in repo["branches"]
            return (200, {"object": {"sha": "abc123"}}) if found else self._answer(404, {"message": "Not Found"}, allow)
        if rest.startswith("git/commits/"):
            return 200, {"tree": {"sha": "tree-base"}}
        if rest == "pulls":
            return 200, repo.get("pulls", [])
        if rest.startswith("branches/"):
            return (200, {"commit": {"sha": "abc123"}}) if rest.split("/")[1] in repo["branches"] else self._answer(404, {"message": "Branch not found"}, allow)
        if rest.startswith("rules/branches/"):
            return 200, repo["rules"]
        if rest.startswith("commits/"):
            full = repo["commits"].get(rest.removeprefix("commits/"))
            return (200, {"sha": full}) if full else self._answer(404, {"message": "No commit found"}, allow)
        if rest.startswith("compare/"):
            head = rest.removeprefix("compare/").split("...", 1)[1]
            if head not in repo["branches"]:
                return self._answer(404, {"message": "Not Found"}, allow)
            return 200, {"ahead_by": repo["ahead"]}
        if rest.startswith("contents/"):
            path = rest.removeprefix("contents/")
            text = repo["files"].get(path)
            if text is not None:
                return 200, {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
            children = [p for p in repo["files"] if p.startswith(f"{path}/")]
            if children:
                return 200, [{"type": "file", "name": p.rsplit("/", 1)[1], "path": p} for p in children]
            return self._answer(404, {"message": "Not Found"}, allow)
        if rest == "labels":
            return 200, [{"name": n["name"], "color": n["color"]} for n in repo["labels"]]
        if rest == "actions/permissions":
            return 200, {"enabled": repo.get("actions_enabled", True)}
        if rest == "actions/workflows":
            return 200, {"total_count": len(repo["workflows"]), "workflows": repo["workflows"]}
        raise AssertionError(path)

    def _write(self, repo, method, rest, body, allow):
        if f"{method} {rest}" in self.forbid:
            return self._answer(403, {"message": "Upgrade to GitHub Pro or make this repository public"}, allow)
        if method == "PUT" and rest.endswith("/protection"):
            repo["protection"] = {"required_pull_request_reviews": body["required_pull_request_reviews"]}
            repo["branches"] = tuple(repo["branches"])
        elif method == "PATCH" and rest.endswith("/protection/required_pull_request_reviews"):
            repo["protection"] = {**repo["protection"], "required_pull_request_reviews": body}
        elif method == "PATCH" and rest == "":
            repo["private"] = body["private"]
        elif method == "POST" and rest == "git/trees":
            return 201, {"sha": "tree-new"}
        elif method == "POST" and rest == "git/commits":
            return 201, {"sha": "commit-new"}
        elif method == "POST" and rest == "pulls":
            pull = {"html_url": f"https://github.com/org/pull/{len(repo.get('pulls', [])) + 1}", "number": 1, "head": body["head"]}
            repo["pulls"] = [*repo.get("pulls", []), pull]
            return 201, pull
        elif method == "POST" and rest == "git/refs":
            repo["branches"] = (*repo["branches"], body["ref"].removeprefix("refs/heads/"))
        elif method == "DELETE" and rest.startswith("labels/"):
            repo["labels"] = [lbl for lbl in repo["labels"] if lbl["name"] != rest.split("/", 1)[1]]
        elif method == "POST" and rest == "labels":
            repo["labels"] = [*repo["labels"], {"name": body["name"], "color": body["color"]}]
        elif method == "PATCH" and rest.startswith("labels/"):
            name = rest.split("/", 1)[1]
            repo["labels"] = [{**lbl, **body} if lbl["name"] == name else lbl for lbl in repo["labels"]]
        elif method == "PUT" and rest == "actions/permissions":
            repo["actions_enabled"] = body["enabled"]
        elif method == "PUT" and rest.startswith("actions/workflows/") and rest.endswith("/enable"):
            repo["workflows"] = [{**w, "state": "active"} for w in repo["workflows"]]
        else:
            raise AssertionError((method, rest))
        return 204, None

    @staticmethod
    def _answer(status, body, allow):
        if status in allow:
            return status, body
        raise ServiceError(f"GitHub respondió {status}: {body.get('message') or 'sin detalle'}", 502)


async def _repos(admin, *names):
    for name in names:
        resp = await admin.post("/api/repositories", json={"key": name, "name": name,
                                                           "githubUrl": f"https://github.com/org/{name}"})
        assert resp.status_code == 201, resp.text


def _setup(monkeypatch, fake):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(settings, "REPO_HEALTH_REFERENCE_REPO", None)
    monkeypatch.setattr(github_service, "rest", fake.rest)
    repo_health_service._report.update(data=None)


async def test_requires_token(admin, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    repo_health_service._report.update(data=None)
    resp = await admin.get("/api/repo-health")
    assert resp.status_code == 503 and "GITHUB_TOKEN" in resp.json()["detail"]


async def test_report_flags_each_standard(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("bueno")
    fake.add("publico", private=False)
    fake.add("sin-bloqueo", protection=None)
    fake.add("sin-labels", labels=[{"name": "Aprobado", "color": "ff0000"}, {"name": "bug", "color": "d73a4a"}])
    fake.add("sin-actions", workflows=())
    fake.add("apagado", workflows=_wf(*WORKFLOWS, state="disabled_manually"))
    fake.add("sin-staging", branches=("master",))
    _setup(monkeypatch, fake)
    await _repos(admin, "apagado", "bueno", "publico", "sin-actions", "sin-bloqueo", "sin-labels", "sin-staging",
                 "fantasma")

    resp = await admin.get("/api/repo-health")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    by_key = {r["key"]: r for r in data["repositories"]}
    status = {k: {c: v["status"] for c, v in r["checks"].items()} for k, r in by_key.items()}

    ok = {"protection": "ok", "staging": "ok", "labels": "ok", "private": "ok", "actions": "ok",
          "claude-md": "ok", "claude-config": "ok", "docs": "unknown",  # sin repo de agentes en el tablero
          "ci-quality": "ok", "staging-protection": "ok", "marketplace-ci": "na"}
    assert status["bueno"] == ok and by_key["bueno"]["status"] == "ok"
    assert status["publico"] == ok | {"private": "fail"} and "público" in by_key["publico"]["checks"]["private"]["message"]
    # Sin protección en ninguna rama: también falla la de staging.
    assert status["sin-bloqueo"] == ok | {"protection": "fail", "staging-protection": "fail"}
    assert "push directo" in by_key["sin-bloqueo"]["checks"]["protection"]["message"]
    assert status["sin-actions"] == ok | {"actions": "fail"}
    assert status["sin-staging"] == ok | {"staging": "fail", "staging-protection": "unknown"} and "staging" in by_key["sin-staging"]["checks"]["staging"]["message"]
    assert status["apagado"] == ok | {"actions": "fail"} and "deshabilitados" in by_key["apagado"]["checks"]["actions"]["message"]
    labels = by_key["sin-labels"]["checks"]["labels"]
    assert labels["status"] == "fail"
    assert labels["missing"] == ["Revisado", "Pendiente", "Waiting", "Breaking Change"] and labels["extra"] == ["bug"]
    assert labels["wrongColor"] == [{"name": "Aprobado", "expected": "1d76db", "actual": "ff0000"}]
    # Un repo que GitHub no encuentra queda "unknown" (no se pudo verificar), no como falla.
    assert by_key["fantasma"]["status"] == "unknown" and by_key["fantasma"]["error"]
    assert data["summary"] == {"ok": 1, "fail": 6, "unknown": 1}
    assert data["branches"] == ["master"]


async def test_protection_variants(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("push-restringido", protection={"restrictions": {"users": []}})
    fake.add("solo-status", protection={"required_status_checks": {}})
    fake.add("ruleset", protection=None, rules=[{"type": "deletion"}, {"type": "pull_request"}])
    fake.add("ruleset-otro", protection=None, rules=[{"type": "deletion"}])
    fake.add("main", branches=("main", "staging"))  # solo master cuenta
    fake.add("sin-rama", branches=("staging",))
    fake.add("sin-permiso", protection="forbidden")
    _setup(monkeypatch, fake)
    await _repos(admin, "push-restringido", "solo-status", "ruleset", "ruleset-otro", "main", "sin-rama", "sin-permiso")

    data = (await admin.get("/api/repo-health")).json()["data"]
    protection = {r["key"]: r["checks"]["protection"]["status"] for r in data["repositories"]}
    assert protection == {
        "push-restringido": "ok", "solo-status": "fail", "ruleset": "ok", "ruleset-otro": "fail",
        "main": "fail", "sin-rama": "fail", "sin-permiso": "unknown",
    }
    # Sin permiso para leer protecciones: el resto de los chequeos sigue andando.
    assert {c: v["status"] for c, v in next(r for r in data["repositories"] if r["key"] == "sin-permiso")["checks"].items()
            }["private"] == "ok"
    assert [r["status"] for r in data["repositories"] if r["key"] == "sin-permiso"] == ["unknown"]


async def test_reference_repo_labels(admin, monkeypatch):
    fake = FakeGithub()
    reference = [{"name": "bug", "color": "d73a4a"}, *STANDARD]
    fake.add("referencia", labels=reference)
    fake.add("igual", labels=reference)
    fake.add("distinto", labels=[{"name": "bug", "color": "000000"}])
    fake.add("de-mas", labels=[*reference, {"name": "wontfix", "color": "ffffff"}])
    _setup(monkeypatch, fake)
    monkeypatch.setattr(settings, "REPO_HEALTH_REFERENCE_REPO", "referencia")
    await _repos(admin, "referencia", "igual", "distinto", "de-mas")

    data = (await admin.get("/api/repo-health")).json()["data"]
    labels = {r["key"]: r["checks"]["labels"] for r in data["repositories"]}
    assert [labels[k]["status"] for k in ("referencia", "igual", "distinto")] == ["ok", "ok", "fail"]
    assert labels["distinto"]["missing"] == [lbl["name"] for lbl in STANDARD]
    assert labels["distinto"]["wrongColor"][0]["name"] == "bug"
    # Exacto: un label de más también es una falla.
    assert labels["de-mas"]["status"] == "fail" and labels["de-mas"]["extra"] == ["wontfix"]
    assert "de más: wontfix" in labels["de-mas"]["message"]
    assert data["referenceRepo"] == "referencia"

    # Si la referencia no está cargada, los labels no se pueden verificar (no es una falla de cada repo).
    monkeypatch.setattr(settings, "REPO_HEALTH_REFERENCE_REPO", "nada")
    data = (await admin.post("/api/repo-health/refresh")).json()["data"]
    assert {r["checks"]["labels"]["status"] for r in data["repositories"]} == {"unknown"}


async def test_report_is_cached_until_refresh(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("uno")
    _setup(monkeypatch, fake)
    await _repos(admin, "uno")

    first = (await admin.get("/api/repo-health")).json()["data"]
    calls = len(fake.calls)
    assert (await admin.get("/api/repo-health")).json()["data"] == first
    assert len(fake.calls) == calls

    fake.repos["org/uno"]["private"] = False
    assert (await admin.get("/api/repo-health")).json()["data"]["summary"]["ok"] == 1
    refreshed = (await admin.post("/api/repo-health/refresh")).json()["data"]
    assert refreshed["summary"] == {"ok": 0, "fail": 1, "unknown": 0}


async def test_monitor_loop_survives_errors(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(settings, "REPO_HEALTH_INTERVAL_SECONDS", 0)
    calls = []

    async def fake_report(db, force=False):
        calls.append(force)
        if len(calls) == 1:
            raise ServiceError("GitHub caído", 502)
        return {"repositories": [{"key": "x", "checks": {"private": {"status": "fail", "message": "Es público"}}}]}

    monkeypatch.setattr(repo_health_service, "report", fake_report)
    task = asyncio.create_task(repo_health_service.monitor_loop())
    for _ in range(100):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert calls[:2] == [True, True]


# --- Arreglos con un token temporal ----------------------------------------------------------

TOKEN = "ghp_temporal_con_permisos_de_admin_123456"


def _bad_repo(fake, name="malo", **extra):
    fake.add(name, private=False, protection=None, branches=("master",),
             labels=[{"name": "aprobado", "color": "ff0000"}, {"name": "bug", "color": "d73a4a"}],
             workflows=_wf("Otro"), **extra)


async def _fix(admin, key, token=TOKEN, **body):
    return await admin.post(f"/api/repo-health/{key}/fix", json={"token": token, **body})


async def test_fix_everything_with_temporary_token(admin, monkeypatch, caplog):
    fake = FakeGithub()
    fake.add("bueno")
    _bad_repo(fake)
    _setup(monkeypatch, fake)
    monkeypatch.setattr(settings, "REPO_HEALTH_REFERENCE_REPO", "bueno")
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_del_backend_sin_admin")
    await _repos(admin, "bueno", "malo")
    before = (await admin.get("/api/repo-health")).json()["data"]
    assert before["summary"] == {"ok": 1, "fail": 1, "unknown": 0}
    fake.writes.clear()
    fake.tokens.clear()

    resp = await _fix(admin, "malo")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    results = {c: r["status"] for c, r in data["results"].items()}
    # Los workflows no se pueden inventar: queda para hacer a mano.
    assert results == {"protection": "fixed", "staging": "fixed", "labels": "fixed", "private": "fixed",
                       "actions": "manual"}
    assert "Build and Push Docker Images" in data["results"]["actions"]["message"]
    repo = fake.repos["org/malo"]
    assert repo["private"] is True
    assert repo["protection"]["required_pull_request_reviews"] == {"required_approving_review_count": 0}
    assert {lbl["name"]: lbl["color"] for lbl in repo["labels"]} == {"aprobado": "1d76db", **{lbl["name"]: lbl["color"] for lbl in STANDARD[1:]}}
    # El label existente se corrige con su nombre real (sin duplicarlo) y el que no es del estándar se borra.
    assert ("PATCH", "malo/labels/aprobado", {"color": "1d76db"}) in fake.writes
    assert [(m, p) for m, p, _ in fake.writes if m == "DELETE"] == [("DELETE", "malo/labels/bug")]
    # staging se crea desde master.
    assert ("POST", "malo/git/refs", {"ref": "refs/heads/staging", "sha": "abc123"}) in fake.writes

    # Todo (arreglos y la revisión posterior) usó el token temporal, no el del backend.
    assert fake.tokens == {TOKEN}
    assert github_service._token_override.get() is None
    assert TOKEN not in resp.text and TOKEN not in caplog.text
    # Se vuelve a revisar el repo: lo que falta es solo Actions, y el resultado en memoria se actualiza.
    assert data["repository"]["checks"]["private"]["status"] == "ok"
    assert data["repository"]["status"] == "fail" and data["repository"]["checks"]["actions"]["status"] == "fail"
    cached = (await admin.get("/api/repo-health")).json()["data"]
    assert next(r for r in cached["repositories"] if r["key"] == "malo") == data["repository"]

    # Idempotente: repetirlo no escribe nada más (salvo lo que sigue pendiente).
    fake.writes.clear()
    again = (await _fix(admin, "malo")).json()["data"]["results"]
    assert set(again) == {"actions"} and fake.writes == []


async def test_fix_keeps_existing_protection(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("con-checks", protection={"required_status_checks": {"strict": True}})
    _setup(monkeypatch, fake)
    await _repos(admin, "con-checks")
    fake.writes.clear()

    results = (await _fix(admin, "con-checks", checks=["protection"])).json()["data"]["results"]
    assert results["protection"]["status"] == "fixed" and list(results) == ["protection"]
    # No se pisa la protección: solo se le agrega el PR obligatorio.
    assert [(m, p.split("/", 1)[1]) for m, p, _ in fake.writes] == [
        ("PATCH", "branches/master/protection/required_pull_request_reviews")]
    assert fake.repos["org/con-checks"]["protection"]["required_status_checks"] == {"strict": True}


async def test_fix_enables_disabled_actions(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("apagado", workflows=_wf(*WORKFLOWS, state="disabled_manually"))
    _setup(monkeypatch, fake)
    await _repos(admin, "apagado")
    results = (await _fix(admin, "apagado")).json()["data"]
    assert results["results"]["actions"] == {"status": "fixed", "message": "Se habilitaron 4 workflow(s)"}
    assert results["repository"]["status"] == "ok"


async def test_fix_reports_github_rejections_and_continues(admin, monkeypatch):
    fake = FakeGithub()
    _bad_repo(fake)
    fake.forbid = {"PUT branches/master/protection"}
    _setup(monkeypatch, fake)
    await _repos(admin, "malo")

    results = (await _fix(admin, "malo")).json()["data"]["results"]
    assert results["protection"]["status"] == "failed" and "Upgrade to GitHub Pro" in results["protection"]["message"]
    assert results["private"]["status"] == "fixed" and results["labels"]["status"] == "fixed"


async def test_fix_validation_and_permissions(admin, monkeypatch):
    fake = FakeGithub()
    _bad_repo(fake)
    _setup(monkeypatch, fake)
    await _repos(admin, "malo")
    before = len(fake.writes)

    assert (await _fix(admin, "malo", token="corto")).status_code == 400
    assert (await _fix(admin, "malo", checks=["rm -rf"])).status_code == 400
    assert (await _fix(admin, "no-existe")).status_code == 404
    # Un token inválido de GitHub se informa sin devolver el token.
    async def unauthorized(method, path, json=None, allow=()):
        raise github_service.ServiceError("El token de GitHub es inválido o venció", 502)
    monkeypatch.setattr(github_service, "rest", unauthorized)
    resp = await _fix(admin, "malo", checks=["private"])
    assert resp.json()["data"]["results"]["private"]["status"] == "failed" and TOKEN not in resp.text
    assert len(fake.writes) == before

    # Solo admins.
    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    resp = await admin.post("/api/repo-health/malo/fix", json={"token": TOKEN}, headers=member)
    assert resp.status_code == 403
    # Y un 422 (cuerpo mal formado) no devuelve el token.
    resp = await admin.post("/api/repo-health/malo/fix", json={"token": TOKEN, "checks": "protection"})
    assert resp.status_code == 422 and TOKEN not in resp.text


# --- Estándar único: labels y workflows del repo de referencia -------------------------------

async def test_standard_is_exposed_and_workflows_are_a_minimum(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("mms-backend")
    fake.add("igual")
    fake.add("incompleto", workflows=_wf(*WORKFLOWS[:3]))
    fake.add("con-extra", workflows=_wf(*WORKFLOWS, "Tests"))
    fake.add("apagado", workflows=[*_wf(WORKFLOWS[0], state="disabled_manually"), *_wf(*WORKFLOWS[1:])])
    _setup(monkeypatch, fake)
    monkeypatch.setattr(settings, "REPO_HEALTH_REFERENCE_REPO", "mms-backend")
    await _repos(admin, "mms-backend", "igual", "incompleto", "con-extra", "apagado")

    data = (await admin.get("/api/repo-health")).json()["data"]
    assert data["standard"] == {
        "referenceRepo": "mms-backend", "branches": ["master"], "stagingBranch": "staging", "private": True,
        "protection": repo_health_service.PROTECTION_RULES, "labels": STANDARD, "labelsExact": True,
        "workflows": WORKFLOWS, "error": None,
        "claude": {"marketplace": "optidev-tools", "plugins": ["task-workflow-tools@optidev-tools"]},
        "docs": {"repo": "agents", "maxCommitsBehind": 50}, "marketplaceWorkflow": "Validate marketplace",
        "checks": [{"key": c, "severity": "info" if c in INFO_CHECKS else "required",
                    "repos": ["agents"] if c == "marketplace-ci" else None} for c in repo_health_service.CHECKS],
    }
    actions = {r["key"]: r["checks"]["actions"] for r in data["repositories"]}
    keys = ("mms-backend", "igual", "incompleto", "con-extra", "apagado")
    assert [actions[k]["status"] for k in keys] == ["ok", "ok", "fail", "ok", "fail"]
    assert actions["incompleto"]["missing"] == ["Promote to Production"]
    # Los workflows de más son información, no un error.
    assert actions["con-extra"]["extra"] == ["Tests"] and "Tests" not in actions["con-extra"]["message"]
    assert actions["incompleto"]["extra"] == [] and actions["apagado"]["disabled"] == [WORKFLOWS[0]]

    # Arreglar: habilita lo deshabilitado; lo que falta no se copia (a dónde apuntan cambia por repo) y los de más no se tocan.
    fake.writes.clear()
    results = (await _fix(admin, "incompleto", checks=["actions"])).json()["data"]["results"]["actions"]
    assert results["status"] == "manual" and "Promote to Production" in results["message"] and fake.writes == []
    results = (await _fix(admin, "apagado", checks=["actions"])).json()["data"]["results"]["actions"]
    assert results["status"] == "fixed" and "habilitaron 1" in results["message"]


async def test_default_standard_without_reference(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("uno", labels=[*STANDARD, {"name": "bug", "color": "d73a4a"}])
    _setup(monkeypatch, fake)
    await _repos(admin, "uno")
    data = (await admin.get("/api/repo-health")).json()["data"]
    standard = data["standard"]
    assert standard["referenceRepo"] is None and standard["labels"] == STANDARD and not standard["labelsExact"]
    assert standard["workflows"] == WORKFLOWS
    # Sin referencia no se exige el set exacto de labels.
    assert data["repositories"][0]["checks"]["labels"]["status"] == "ok"


async def test_monitor_waits_before_first_check(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(settings, "REPO_HEALTH_INTERVAL_SECONDS", 3600)
    calls = []

    async def fake_report(db, force=False):
        calls.append(force)
        return {"repositories": []}

    monkeypatch.setattr(repo_health_service, "report", fake_report)
    task = asyncio.create_task(repo_health_service.monitor_loop())
    await asyncio.sleep(0.1)
    task.cancel()
    assert calls == []  # al arrancar no revisa: la primera vista lo hace y queda en caché


# --- Severidad y aplicabilidad de los chequeos -----------------------------------------------

SPECS = repo_health_service.CHECK_SPECS
INFO_CHECKS = ("claude-md", "claude-config", "docs", "ci-quality", "staging-protection", "marketplace-ci")


async def test_info_check_does_not_fail_the_repo(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("sin-staging", branches=("master",))
    _setup(monkeypatch, fake)
    monkeypatch.setitem(SPECS, "staging", {"severity": "info"})
    await _repos(admin, "sin-staging")

    data = (await admin.get("/api/repo-health")).json()["data"]
    repo = data["repositories"][0]
    # Falla, pero es informativo: el repo sigue cumpliendo y el estándar expone la severidad.
    assert repo["checks"]["staging"]["status"] == "fail" and repo["checks"]["staging"]["severity"] == "info"
    assert repo["status"] == "ok" and data["summary"] == {"ok": 1, "fail": 0, "unknown": 0}
    assert data["checkSummary"]["staging"] == {"ok": 0, "fail": 1, "unknown": 0, "na": 0}
    assert data["checkSummary"]["private"] == {"ok": 1, "fail": 0, "unknown": 0, "na": 0}
    assert {"key": "staging", "severity": "info", "repos": None} in data["standard"]["checks"]


async def test_check_limited_to_some_repos_is_not_applicable_elsewhere(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("uno")
    fake.add("dos", branches=("master",))
    _setup(monkeypatch, fake)
    monkeypatch.setitem(SPECS, "staging", {"severity": "required", "repos": ["uno"]})
    monkeypatch.setitem(SPECS, "staging-protection", {"severity": "info", "repos": ["uno"]})  # también mira staging
    await _repos(admin, "uno", "dos")

    data = (await admin.get("/api/repo-health")).json()["data"]
    by_key = {r["key"]: r for r in data["repositories"]}
    staging = by_key["dos"]["checks"]["staging"]
    assert staging["status"] == "na" and "uno" in staging["message"]
    assert by_key["dos"]["status"] == "ok" and by_key["uno"]["checks"]["staging"]["status"] == "ok"
    # Donde no aplica ni se consulta a GitHub.
    assert "/repos/org/dos/branches/staging" not in fake.calls
    assert data["checkSummary"]["staging"] == {"ok": 1, "fail": 0, "unknown": 0, "na": 1}


async def test_fix_all_skips_info_and_not_applicable_checks(admin, monkeypatch):
    fake = FakeGithub()
    _bad_repo(fake)
    _setup(monkeypatch, fake)
    monkeypatch.setitem(SPECS, "private", {"severity": "info"})
    monkeypatch.setitem(SPECS, "labels", {"severity": "required", "repos": ["otro"]})
    await _repos(admin, "malo")
    fake.writes.clear()

    results = (await _fix(admin, "malo")).json()["data"]["results"]
    assert set(results) == {"protection", "staging", "actions"}
    assert fake.repos["org/malo"]["private"] is False
    # Un arreglo pedido a mano sí se aplica aunque el chequeo sea informativo.
    assert (await _fix(admin, "malo", checks=["private"])).json()["data"]["results"]["private"]["status"] == "fixed"
    cached = (await admin.get("/api/repo-health")).json()["data"]
    assert cached["checkSummary"]["protection"]["ok"] == 1 and cached["checkSummary"]["private"]["ok"] == 1


async def test_fix_of_unreadable_repo_reports_failures(admin, monkeypatch):
    fake = FakeGithub()
    _setup(monkeypatch, fake)
    await _repos(admin, "fantasma")

    # Un repo que GitHub no encuentra no rompe el arreglo: se intenta todo lo exigido y cada uno informa su resultado.
    resp = await _fix(admin, "fantasma")
    assert resp.status_code == 200, resp.text
    required = {c for c, spec in SPECS.items() if spec["severity"] == "required"}
    assert set(resp.json()["data"]["results"]) == required


def test_log_failures_ignores_info_checks(caplog):
    checks = {"private": {"status": "fail", "severity": "info", "message": "Es público"},
              "staging": {"status": "fail", "severity": "required", "message": "Falta staging"}}
    with caplog.at_level("WARNING"):
        repo_health_service._log_failures({"repositories": [{"key": "x", "checks": checks}]})
    assert "staging: Falta staging" in caplog.text and "Es público" not in caplog.text


# --- Estándar de agentes: CLAUDE.md y config de Claude ---------------------------------------


async def test_claude_md_check(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("bien")
    fake.add("sin-archivo", files={})
    fake.add("sin-enlace", files={"CLAUDE.md": "# sin-enlace\n\nSolo texto\n"})
    fake.add("sin-seccion", files={"CLAUDE.md": "Ver repo-docs/sin-seccion\n"})
    fake.add("mms", files={"CLAUDE.md": "Ver repo-docs/mms-backend\n\n## Verificar\n"})
    fake.add("con-verificacion", files={"CLAUDE.md": "Ver repo-docs/con-verificacion\n\n## Verificación\n\nyarn test\n"})
    _setup(monkeypatch, fake)
    await _repos(admin, "bien", "sin-archivo", "sin-enlace", "sin-seccion", "mms", "con-verificacion")

    data = (await admin.get("/api/repo-health")).json()["data"]
    checks = {r["key"]: r["checks"]["claude-md"] for r in data["repositories"]}
    assert {k: c["status"] for k, c in checks.items()} == {
        "bien": "ok", "sin-archivo": "fail", "sin-enlace": "fail", "sin-seccion": "fail", "mms": "fail",
        "con-verificacion": "ok"}
    assert checks["sin-archivo"]["message"] == "Falta CLAUDE.md en la raíz del repo"
    assert "repo-docs/sin-enlace" in checks["sin-enlace"]["message"] and "«Verificar»" in checks["sin-enlace"]["message"]
    assert "repo-docs" not in checks["sin-seccion"]["message"] and "«Verificar»" in checks["sin-seccion"]["message"]
    # Mencionar repo-docs/mms-backend no vale como enlace a repo-docs/mms.
    assert "repo-docs/mms" in checks["mms"]["message"] and "«Verificar»" not in checks["mms"]["message"]
    # Es informativo: ningún repo queda en falla por esto.
    assert {r["status"] for r in data["repositories"]} == {"ok"}
    assert data["checkSummary"]["claude-md"] == {"ok": 2, "fail": 4, "unknown": 0, "na": 0}


async def test_claude_config_check(admin, monkeypatch):
    plugin = "task-workflow-tools@optidev-tools"
    marketplace = {"optidev-tools": {}}

    def config(**settings_json):
        return {".claude/settings.json": json.dumps(settings_json)}

    fake = FakeGithub()
    fake.add("bien")
    fake.add("sin-archivo", files={})
    fake.add("json-roto", files={".claude/settings.json": "{no es json"})
    fake.add("no-es-objeto", files={".claude/settings.json": "[]"})
    fake.add("sin-marketplace", files=config(enabledPlugins={plugin: True}))
    fake.add("plugin-apagado", files=config(extraKnownMarketplaces=marketplace, enabledPlugins={plugin: False}))
    fake.add("otras-claves", files=config(extraKnownMarketplaces=marketplace, worktree={"baseRef": "head"},
                                          enabledPlugins={plugin: True, "otro@mercado": True}))
    _setup(monkeypatch, fake)
    await _repos(admin, "bien", "sin-archivo", "json-roto", "no-es-objeto", "sin-marketplace", "plugin-apagado",
                 "otras-claves")

    data = (await admin.get("/api/repo-health")).json()["data"]
    checks = {r["key"]: r["checks"]["claude-config"] for r in data["repositories"]}
    assert {k: c["status"] for k, c in checks.items()} == {
        "bien": "ok", "sin-archivo": "fail", "json-roto": "fail", "no-es-objeto": "fail", "sin-marketplace": "fail",
        "plugin-apagado": "fail", "otras-claves": "ok"}
    assert checks["sin-archivo"]["message"] == "Falta .claude/settings.json"
    assert "JSON válido" in checks["json-roto"]["message"]
    assert "marketplace optidev-tools" in checks["no-es-objeto"]["message"] and plugin in checks["no-es-objeto"]["message"]
    assert "marketplace" in checks["sin-marketplace"]["message"] and plugin not in checks["sin-marketplace"]["message"]
    assert plugin in checks["plugin-apagado"]["message"] and "marketplace" not in checks["plugin-apagado"]["message"]
    assert {r["status"] for r in data["repositories"]} == {"ok"}


def _pr_writes(fake, name):
    """(commit sobre staging, árbol con los archivos, PR) de las escrituras de un repo."""
    writes = [w for w in fake.writes if w[1].startswith(f"{name}/")]
    tree = next(w[2] for w in writes if w[1].endswith("git/trees"))
    pull = next(w[2] for w in writes if w[1].endswith("/pulls"))
    return writes, {t["path"]: t["content"] for t in tree["tree"]}, pull


async def test_fix_claude_md_opens_pr_with_missing_pieces(admin, monkeypatch):
    fake = FakeGithub()
    package = json.dumps({"scripts": {"test": "vitest", "build": "vite build", "dev": "vite"}})
    fake.add("web", files={"CLAUDE.md": "# web\n\nNotas propias.\n", "package.json": package, "yarn.lock": ""})
    fake.add(settings.WORKFLOW_REPO_KEY)
    _setup(monkeypatch, fake)
    await _repos(admin, "web", settings.WORKFLOW_REPO_KEY)
    fake.writes.clear()

    results = (await _fix(admin, "web", checks=["claude-md"])).json()["data"]["results"]
    assert results["claude-md"]["status"] == "fixed" and "https://github.com/org/pull/1" in results["claude-md"]["message"]
    _, files, pull = _pr_writes(fake, "web")
    text = files["CLAUDE.md"]
    assert text.startswith("# web\n\nNotas propias.\n")  # conserva lo que ya tenía
    assert "repo-docs/web" in text and f"github.com/org/{settings.WORKFLOW_REPO_KEY}/tree/HEAD/repo-docs/web" in text
    assert "## Verificar" in text and "yarn test\nyarn build" in text and "yarn dev" not in text
    assert pull["base"] == "staging" and pull["head"] == "estandar/claude-md"
    # Nada se commiteó a la rama protegida: solo se crea la rama del PR.
    assert [w[2]["ref"] for w in fake.writes if w[1].endswith("git/refs")] == ["refs/heads/estandar/claude-md"]


async def test_fix_claude_md_without_known_commands_leaves_verify_manual(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("sin-nada", files={})
    _setup(monkeypatch, fake)
    await _repos(admin, "sin-nada")
    fake.writes.clear()

    result = (await _fix(admin, "sin-nada", checks=["claude-md"])).json()["data"]["results"]["claude-md"]
    # Se abre el PR con el enlace, pero «Verificar» no se inventa.
    assert result["status"] == "manual" and "Verificar" in result["message"] and "pull/1" in result["message"]
    _, files, _ = _pr_writes(fake, "sin-nada")
    assert "repo-docs/sin-nada" in files["CLAUDE.md"] and "Verificar" not in files["CLAUDE.md"]


async def test_fix_claude_config_merges_settings_and_keeps_other_keys(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("helios", files={".claude/settings.json": json.dumps({
        "worktree": {"symlinkDirectories": ["node_modules"]}, "enabledPlugins": {"otro@mkt": True}})})
    fake.add(settings.WORKFLOW_REPO_KEY)
    _setup(monkeypatch, fake)
    await _repos(admin, "helios", settings.WORKFLOW_REPO_KEY)
    fake.writes.clear()

    result = (await _fix(admin, "helios", checks=["claude-config"])).json()["data"]["results"]["claude-config"]
    assert result["status"] == "fixed"
    config = json.loads(_pr_writes(fake, "helios")[1][".claude/settings.json"])
    assert config["worktree"] == {"symlinkDirectories": ["node_modules"]}
    assert config["enabledPlugins"] == {"otro@mkt": True, "task-workflow-tools@optidev-tools": True}
    assert config["extraKnownMarketplaces"]["optidev-tools"]["source"] == {
        "source": "github", "repo": f"org/{settings.WORKFLOW_REPO_KEY}"}


async def test_fix_claude_config_does_not_overwrite_invalid_json(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("roto", files={".claude/settings.json": "{no es json"})
    fake.add(settings.WORKFLOW_REPO_KEY)
    _setup(monkeypatch, fake)
    await _repos(admin, "roto", settings.WORKFLOW_REPO_KEY)
    fake.writes.clear()

    result = (await _fix(admin, "roto", checks=["claude-config"])).json()["data"]["results"]["claude-config"]
    assert result["status"] == "failed" and "JSON" in result["message"]
    assert fake.writes == []


async def test_file_fix_reuses_open_pull_request_and_needs_staging(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("con-pr", files={}, branches=("master", "staging", "estandar/claude-config"))
    fake.repos["org/con-pr"]["pulls"] = [{"html_url": "https://github.com/org/con-pr/pull/9", "number": 9}]
    fake.add("sin-staging", files={}, branches=("master",))
    fake.add(settings.WORKFLOW_REPO_KEY)
    _setup(monkeypatch, fake)
    await _repos(admin, "con-pr", "sin-staging", settings.WORKFLOW_REPO_KEY)
    fake.writes.clear()

    result = (await _fix(admin, "con-pr", checks=["claude-config"])).json()["data"]["results"]["claude-config"]
    assert result["status"] == "ok" and "pull/9" in result["message"] and fake.writes == []
    result = (await _fix(admin, "sin-staging", checks=["claude-config"])).json()["data"]["results"]["claude-config"]
    assert result["status"] == "failed" and "staging" in result["message"] and fake.writes == []


async def test_fix_docs_stays_manual(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("sin-nada", files={})
    _setup(monkeypatch, fake)
    await _repos(admin, "sin-nada")
    fake.writes.clear()

    result = (await _fix(admin, "sin-nada", checks=["docs"])).json()["data"]["results"]["docs"]
    assert result["status"] == "manual" and "repo-docs/sin-nada" in result["message"]
    assert fake.writes == []
    # "Arreglar todo" no incluye los informativos.
    assert (await _fix(admin, "sin-nada")).json()["data"]["results"] == {}


async def test_pending_card_lists_failing_checks_once(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("incompleto", files={}, workflows=())
    fake.add("bueno")
    fake.add(settings.WORKFLOW_REPO_KEY, files={"repo-docs/bueno/README.md": _doc("bueno")})
    _setup(monkeypatch, fake)
    await _repos(admin, "incompleto", "bueno", settings.WORKFLOW_REPO_KEY)

    resp = await admin.post("/api/repo-health/incompleto/card")
    assert resp.status_code == 201, resp.text
    card = resp.json()["data"]
    assert card["created"] is True and card["card"]["title"] == "Dejar incompleto en el estándar"
    assert [r["key"] for r in card["card"]["repositories"]] == ["incompleto"]
    assert "CLAUDE.md" in card["card"]["description"] and "GitHub Actions" in card["card"]["description"]
    # Dos veces no duplica la tarjeta.
    again_resp = await admin.post("/api/repo-health/incompleto/card")
    again = again_resp.json()
    assert again_resp.status_code == 200 and again["data"]["created"] is False and again["data"]["card"]["id"] == card["card"]["id"]
    # Un repo que cumple todo no genera tarjeta; uno inexistente, 404.
    assert (await admin.post("/api/repo-health/bueno/card")).status_code == 400
    assert (await admin.post("/api/repo-health/nada/card")).status_code == 404


async def test_pending_card_is_admin_only(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("incompleto", files={})
    _setup(monkeypatch, fake)
    await _repos(admin, "incompleto")
    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    assert (await admin.post("/api/repo-health/incompleto/card", headers=member)).status_code == 403


# --- Labels del flujo de PRs: siempre parte del estándar, con los colores de mms-backend ----------


async def test_flow_labels_are_always_part_of_the_standard(admin, monkeypatch):
    fake = FakeGithub()
    # El repo de referencia no tiene los labels del flujo (y su "aprobado" va con otro color y en minúscula).
    reference = [{"name": "bug", "color": "d73a4a"}, {"name": "aprobado", "color": "ff0000"}]
    fake.add("referencia", labels=reference)
    _setup(monkeypatch, fake)
    monkeypatch.setattr(settings, "REPO_HEALTH_REFERENCE_REPO", "referencia")
    await _repos(admin, "referencia")

    data = (await admin.get("/api/repo-health")).json()["data"]
    # Se respeta lo del repo de referencia y se suman los del flujo que le faltan (sin duplicar "aprobado").
    assert data["standard"]["labels"] == [*reference, *STANDARD[1:]]
    # Ni siquiera el repo de referencia cumple si le faltan.
    check = data["repositories"][0]["checks"]["labels"]
    assert check["status"] == "fail" and check["missing"] == [lbl["name"] for lbl in STANDARD[1:]]


async def test_waiting_label_with_another_color_is_flagged(admin, monkeypatch):
    # Caso real: optidev tenía "Waiting" en ededed; mms-backend lo tiene en f9d0c4.
    fake = FakeGithub()
    fake.add("uno", labels=[*STANDARD[:3], {"name": "Waiting", "color": "ededed"}, STANDARD[4]])
    _setup(monkeypatch, fake)
    await _repos(admin, "uno")

    labels = (await admin.get("/api/repo-health")).json()["data"]["repositories"][0]["checks"]["labels"]
    assert labels["status"] == "fail"
    assert labels["wrongColor"] == [{"name": "Waiting", "expected": "f9d0c4", "actual": "ededed"}]


# --- Estándar de agentes: documentación en repo-docs -----------------------------------------


async def test_docs_check(admin, monkeypatch):
    docs = {f"repo-docs/{name}/README.md": _doc(name) for name in ("al-limite", "atrasado")}
    docs["repo-docs/sin-front/README.md"] = "# sin front matter\n"
    docs["repo-docs/commit-raro/README.md"] = _doc("commit-raro", sha="ffffff0")
    docs["repo-docs/rama-rara/README.md"] = _doc("rama-rara", branch="desaparecida")
    fake = FakeGithub()
    fake.add("agents", files=docs)
    fake.add("al-limite", ahead=50)
    fake.add("atrasado", ahead=51)
    for name in ("sin-doc", "sin-front", "commit-raro", "rama-rara"):
        fake.add(name)
    _setup(monkeypatch, fake)
    await _repos(admin, "agents", "al-limite", "atrasado", "sin-doc", "sin-front", "commit-raro", "rama-rara")

    data = (await admin.get("/api/repo-health")).json()["data"]
    checks = {r["key"]: r["checks"]["docs"] for r in data["repositories"]}
    assert {k: c["status"] for k, c in checks.items()} == {
        "agents": "fail", "al-limite": "ok", "atrasado": "fail", "sin-doc": "fail", "sin-front": "fail",
        "commit-raro": "fail", "rama-rara": "fail"}
    assert checks["al-limite"]["behind"] == 50 and "50 commits" in checks["al-limite"]["message"]
    assert "51 commits" in checks["atrasado"]["message"] and "máximo 50" in checks["atrasado"]["message"]
    assert "repo-docs/sin-doc/README.md" in checks["sin-doc"]["message"]
    assert "documentado_en_commit" in checks["sin-front"]["message"]
    assert "ffffff0" in checks["commit-raro"]["message"]
    assert "desaparecida" in checks["rama-rara"]["message"]
    # Es informativo: ningún repo queda en falla por esto.
    assert {r["status"] for r in data["repositories"]} == {"ok"}


async def test_docs_unknown_when_docs_repo_is_not_loaded(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("uno")
    _setup(monkeypatch, fake)
    await _repos(admin, "uno")

    check = (await admin.get("/api/repo-health")).json()["data"]["repositories"][0]["checks"]["docs"]
    assert check["status"] == "unknown" and "'agents'" in check["message"]


# --- Estándar de agentes: CI, staging protegido y CI del marketplace -------------------------------


def _workflow(*commands):
    steps = "".join(f"      - run: {command}\n" for command in commands)
    return {".github/workflows/ci.yml": f"name: CI\njobs:\n  ci:\n    steps:\n{steps}"}


def _check(data, key, check):
    return next(r["checks"][check] for r in data["repositories"] if r["key"] == key)


async def test_ci_quality_check(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("bien")
    fake.add("sin-workflows", files={})
    fake.add("solo-tests", files=_workflow("yarn test"))
    fake.add("solo-lint", files=_workflow("npx eslint src"))
    fake.add("en-dos-archivos", files={**_workflow("pytest -q"), ".github/workflows/lint.yaml": "run: ruff check .\n"})
    fake.add("sin-nada", files=_workflow("echo hola"))
    _setup(monkeypatch, fake)
    await _repos(admin, "bien", "sin-workflows", "solo-tests", "solo-lint", "en-dos-archivos", "sin-nada")

    data = (await admin.get("/api/repo-health")).json()["data"]
    keys = ("bien", "sin-workflows", "solo-tests", "solo-lint", "en-dos-archivos", "sin-nada")
    assert [_check(data, k, "ci-quality")["status"] for k in keys] == ["ok", "fail", "fail", "fail", "ok", "fail"]
    assert "No tiene workflows" in _check(data, "sin-workflows", "ci-quality")["message"]
    assert _check(data, "solo-tests", "ci-quality")["message"] == "El CI no corre lint"
    assert _check(data, "solo-lint", "ci-quality")["message"] == "El CI no corre tests"
    assert _check(data, "sin-nada", "ci-quality")["message"] == "El CI no corre tests ni lint"
    # Es informativo: ningún repo queda en falla por esto.
    assert {r["status"] for r in data["repositories"]} == {"ok"}


async def test_fix_ci_quality_opens_pr_with_missing_steps(admin, monkeypatch):
    fake = FakeGithub()
    package = json.dumps({"scripts": {"test": "vitest", "lint": "eslint .", "build": "vite build"}})
    fake.add("web", files={"package.json": package, "yarn.lock": ""})
    fake.add("api", files={**_workflow("echo hola"), "requirements.txt": "fastapi\npytest\nruff\n"})
    fake.add("solo-lint", files={".github/workflows/tests.yml": "run: yarn test\n", "package.json": package, "yarn.lock": ""})
    _setup(monkeypatch, fake)
    await _repos(admin, "web", "api", "solo-lint")
    fake.writes.clear()

    result = (await _fix(admin, "web", checks=["ci-quality"])).json()["data"]["results"]["ci-quality"]
    assert result["status"] == "fixed" and "pull/1" in result["message"]
    _, files, pull = _pr_writes(fake, "web")
    workflow = files[".github/workflows/ci.yml"]
    assert "yarn install --frozen-lockfile\n      - run: yarn test\n      - run: yarn lint" in workflow
    assert "yarn build" not in workflow and "branches: [staging, master]" in workflow
    assert pull["base"] == "staging" and pull["head"] == "estandar/ci-quality"

    # Con un workflow existente que no es ci.yml, agrega ci.yml solo con lo que falta (y Python se deduce de requirements).
    fake.repos["org/api"]["files"][".github/workflows/otro.yml"] = fake.repos["org/api"]["files"].pop(".github/workflows/ci.yml")
    await _fix(admin, "api", checks=["ci-quality"])
    api = _pr_writes(fake, "api")[1][".github/workflows/ci.yml"]
    assert "pip install -r requirements.txt\n      - run: pytest -q\n      - run: ruff check ." in api and "node" not in api

    await _fix(admin, "solo-lint", checks=["ci-quality"])
    only_lint = _pr_writes(fake, "solo-lint")[1][".github/workflows/ci.yml"]
    assert "yarn lint" in only_lint and "yarn test" not in only_lint


async def test_fix_ci_quality_stays_manual_when_it_cannot_be_deduced(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("monorepo", files={})
    fake.add("pnpm", files={"package.json": json.dumps({"scripts": {"test": "x", "lint": "y"}}), "pnpm-lock.yaml": ""})
    fake.add("con-ci", files=_workflow("yarn test"))
    fake.add("parcial", files={"package.json": json.dumps({"scripts": {"test": "x"}}), "yarn.lock": ""})
    _setup(monkeypatch, fake)
    await _repos(admin, "monorepo", "pnpm", "con-ci", "parcial")
    fake.writes.clear()

    for key in ("monorepo", "pnpm"):
        result = (await _fix(admin, key, checks=["ci-quality"])).json()["data"]["results"]["ci-quality"]
        assert result["status"] == "manual" and "tests y lint" in result["message"]
    result = (await _fix(admin, "con-ci", checks=["ci-quality"])).json()["data"]["results"]["ci-quality"]
    assert result["status"] == "manual" and "ci.yml" in result["message"] and "lint" in result["message"]
    assert fake.writes == []
    # Abre el PR con lo que sí se dedujo (tests) y avisa de lo que falta (lint).
    result = (await _fix(admin, "parcial", checks=["ci-quality"])).json()["data"]["results"]["ci-quality"]
    assert result["status"] == "manual" and "pull/1" in result["message"] and "lint" in result["message"]
    assert "yarn lint" not in _pr_writes(fake, "parcial")[1][".github/workflows/ci.yml"]


async def test_staging_protection_check(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("bien")
    fake.add("staging-abierto", staging_protection=None)
    fake.add("sin-staging", branches=("master",))
    fake.add("sin-permiso", staging_protection="forbidden")
    _setup(monkeypatch, fake)
    await _repos(admin, "bien", "staging-abierto", "sin-staging", "sin-permiso")

    data = (await admin.get("/api/repo-health")).json()["data"]
    keys = ("bien", "staging-abierto", "sin-staging", "sin-permiso")
    assert [_check(data, k, "staging-protection")["status"] for k in keys] == ["ok", "fail", "unknown", "unknown"]
    abierto = _check(data, "staging-abierto", "staging-protection")
    assert "push directo" in abierto["message"] and abierto["branch"] == "staging"
    # Sin rama staging ya falla el chequeo de la rama: este queda sin verificar para no contarlo dos veces.
    assert _check(data, "sin-staging", "staging")["status"] == "fail"
    assert {r["status"] for r in data["repositories"] if r["key"] != "sin-staging"} == {"ok"}


async def test_marketplace_ci_check_applies_only_to_the_agents_repo(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("agents", protection={**PROTECTED, "required_status_checks": {"contexts": ["validate"]}},
             workflows=_wf("Validate marketplace"))
    fake.add("otro")
    _setup(monkeypatch, fake)
    await _repos(admin, "agents", "otro")
    agents = fake.repos["org/agents"]

    data = (await admin.get("/api/repo-health")).json()["data"]
    assert _check(data, "agents", "marketplace-ci")["status"] == "ok"
    assert _check(data, "otro", "marketplace-ci")["status"] == "na" and "agents" in _check(data, "otro", "marketplace-ci")["message"]

    agents.update(protection=PROTECTED, workflows=_wf("Otro"))  # sin status checks requeridos y sin el workflow
    data = (await admin.post("/api/repo-health/refresh")).json()["data"]
    check = _check(data, "agents", "marketplace-ci")
    assert check["status"] == "fail" and "no exige status checks" in check["message"]
    assert "Validate marketplace" in check["message"]

    # También vale que lo exija un ruleset.
    agents.update(protection=None, rules=[{"type": "pull_request"}, {"type": "required_status_checks"}],
                  workflows=_wf("Validate marketplace"))
    data = (await admin.post("/api/repo-health/refresh")).json()["data"]
    assert _check(data, "agents", "marketplace-ci")["status"] == "ok"

    agents.update(protection="forbidden", rules=[])  # sin permiso de admin no se puede saber
    data = (await admin.post("/api/repo-health/refresh")).json()["data"]
    assert _check(data, "agents", "marketplace-ci")["status"] == "unknown"
    assert {r["status"] for r in data["repositories"] if r["key"] == "otro"} == {"ok"}


async def test_fix_rejects_checks_that_do_not_apply(admin, monkeypatch):
    fake = FakeGithub()
    fake.add("otro")
    _setup(monkeypatch, fake)
    await _repos(admin, "otro")
    fake.writes.clear()

    resp = await _fix(admin, "otro", checks=["marketplace-ci"])
    assert resp.status_code == 400 and "marketplace-ci" in resp.json()["detail"]
    assert fake.writes == []
    # Los que sí aplican siguen funcionando (a mano, sin escribir en GitHub).
    results = (await _fix(admin, "otro", checks=["docs", "staging-protection"])).json()["data"]["results"]
    assert {c: r["status"] for c, r in results.items()} == {"docs": "manual", "staging-protection": "manual"}
    assert "pr-review-merge" in results["staging-protection"]["message"] and fake.writes == []


# --- Revisión automática al cargar un repo ---------------------------------------------------

REAL_CHECK_NEW_REPOSITORY = repo_health_service.check_new_repository  # antes de que conftest la desactive


@pytest.fixture
def review_on_create(monkeypatch):
    """Activa la revisión al cargar un repo (la suite la tiene desactivada por defecto)."""
    monkeypatch.setattr(repo_health_service, "check_new_repository", REAL_CHECK_NEW_REPOSITORY)


async def _alerts(admin):
    items = (await admin.get("/api/notifications")).json()["data"]["items"]
    return [n for n in items if n["kind"] == "repo_out_of_standard"]


async def test_new_repository_is_reviewed_and_admins_are_notified(admin, monkeypatch, review_on_create):
    fake = FakeGithub()
    fake.add("bueno")
    fake.add("malo", private=False, protection=None)
    fake.add("bueno-2")
    _setup(monkeypatch, fake)
    await _repos(admin, "bueno")
    first = (await admin.get("/api/repo-health")).json()["data"]
    assert [r["key"] for r in first["repositories"]] == ["bueno"]
    assert await _alerts(admin) == []  # el repo cumple: no hay nada que avisar

    calls = len(fake.calls)
    await _repos(admin, "malo", "bueno-2")
    assert len(fake.calls) > calls  # se revisó al cargarlo, sin esperar a que alguien abra la pestaña
    calls = len(fake.calls)
    cached = (await admin.get("/api/repo-health")).json()["data"]
    assert len(fake.calls) == calls  # y el resultado quedó en el reporte en memoria
    assert {r["key"]: r["status"] for r in cached["repositories"]} == {"bueno": "ok", "malo": "fail", "bueno-2": "ok"}
    assert cached["summary"] == {"ok": 2, "fail": 1, "unknown": 0}

    # Solo el que no cumple avisa, con qué falla (solo lo exigido; lo informativo no cuenta).
    (alert,) = await _alerts(admin)
    assert alert["data"] == {"key": "malo", "name": "malo", "failing": ["protection", "private"], "unknown": []}


async def test_unreadable_new_repository_is_reported_as_unverified(admin, monkeypatch, review_on_create):
    fake = FakeGithub()
    _setup(monkeypatch, fake)
    await _repos(admin, "fantasma")  # GitHub no lo encuentra

    (alert,) = await _alerts(admin)
    assert alert["data"]["failing"] == [] and set(alert["data"]["unknown"]) == {
        "protection", "staging", "labels", "private", "actions"}


async def test_review_runs_again_only_when_the_github_url_changes(admin, monkeypatch, review_on_create):
    fake = FakeGithub()
    fake.add("uno")
    fake.add("otro")
    _setup(monkeypatch, fake)
    await _repos(admin, "uno")
    repo = next(r for r in (await admin.get("/api/repositories")).json()["data"] if r["key"] == "uno")
    body = {"key": "uno", "name": "uno", "githubUrl": "https://github.com/org/uno", "defaultBranch": "staging"}
    calls = len(fake.calls)

    assert (await admin.put(f"/api/repositories/{repo['id']}", json={**body, "name": "Uno"})).status_code == 200
    assert len(fake.calls) == calls  # otro nombre: no se vuelve a revisar

    other = {**body, "githubUrl": "https://github.com/org/otro"}
    assert (await admin.put(f"/api/repositories/{repo['id']}", json=other)).status_code == 200
    assert any(path.startswith("/repos/org/otro") for path in fake.calls[calls:])


async def test_creating_a_repository_never_depends_on_the_review(admin, monkeypatch, review_on_create):
    fake = FakeGithub()
    fake.add("uno")
    _setup(monkeypatch, fake)

    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)  # sin token no hay con qué revisar
    await _repos(admin, "uno")
    assert fake.calls == [] and await _alerts(admin) == []

    async def broken(*args, **kwargs):
        raise RuntimeError("falla inesperada")

    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(repo_health_service, "check_item", broken)
    await _repos(admin, "dos")  # el alta responde 201 igual (lo verifica _repos)
    assert {r["key"] for r in (await admin.get("/api/repositories")).json()["data"]} == {"uno", "dos"}
