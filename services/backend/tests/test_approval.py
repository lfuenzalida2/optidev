"""Aprobar QA pone el label "Aprobado" en los PRs; rechazar lo devuelve a "Revisado" (GitHub mockeado)."""

import pytest

from src.config.settings import settings
from src.services import github_service
from src.services.errors import ServiceError
from tests.conftest import USER_TOKEN
from tests.test_board import _list_id, _new_card

PR1 = "https://github.com/org/helios/pull/7"
PR2 = "https://github.com/org/web/pull/3"


class FakeGithub:
    """API REST de GitHub en memoria: PRs, labels de cada repo y llamadas hechas."""

    def __init__(self):
        self.pulls = {}  # (owner, repo, n) -> dict
        self.repo_labels = {}  # (owner, repo) -> set
        self.calls = []
        self.fail = {}  # (method, path) -> (status, message)

    def add_pull(self, owner, repo, number, labels=(), state="open", merged=False, **extra):
        self.pulls[(owner, repo, number)] = {
            "number": number, "state": state, "merged_at": "2026-01-01T00:00:00Z" if merged else None,
            "labels": [{"name": n} for n in labels], **extra,
        }

    async def rest(self, method, path, json=None, allow=(), token=None):
        self.calls.append((method, path, json))
        # Leer va con el token de la app; todo lo que escribe, con el del usuario.
        assert (token == USER_TOKEN) == (method != "GET" or "/labels/" in path), f"{method} {path} con token {token}"
        status, body = self._handle(method, path, json)
        if status >= 400 and status not in allow:
            raise ServiceError(f"GitHub respondió {status}: {body.get('message')}", 502)
        return status, body

    def _handle(self, method, path, json):
        if (method, path) in self.fail:
            status, msg = self.fail[(method, path)]
            return status, {"message": msg}
        parts = path.strip("/").split("/")
        owner, repo = parts[1], parts[2]
        if parts[3] == "pulls" and method == "GET":
            pr = self.pulls.get((owner, repo, int(parts[4])))
            return (200, pr) if pr else (404, {"message": "Not Found"})
        if parts[3] == "labels" and method == "GET":
            exists = parts[4] in self.repo_labels.get((owner, repo), set())
            return (200, {"name": parts[4]}) if exists else (404, {"message": "Not Found"})
        if parts[3] == "labels" and method == "POST":
            self.repo_labels.setdefault((owner, repo), set()).add(json["name"])
            return 201, {"name": json["name"]}
        if parts[3] == "issues" and parts[5] == "labels" and method == "POST":
            pr = self.pulls[(owner, repo, int(parts[4]))]
            pr["labels"] += [{"name": n} for n in json["labels"]]
            return 200, pr["labels"]
        if parts[3] == "issues" and parts[5] == "labels" and method == "DELETE":
            pr = self.pulls[(owner, repo, int(parts[4]))]
            pr["labels"] = [lbl for lbl in pr["labels"] if lbl["name"] != parts[6]]
            return 200, pr["labels"]
        raise AssertionError(f"llamada inesperada: {method} {path}")


@pytest.fixture
def fake_github(monkeypatch, github_user):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    fake = FakeGithub()
    monkeypatch.setattr(github_service, "rest", fake.rest)
    return fake


async def _card_in(admin, list_name, urls):
    card = await _new_card(admin, await _list_id(admin, list_name))
    await admin.patch(f"/api/cards/{card['id']}", json={"prUrls": urls})
    return card


async def test_approve_labels_prs_and_keeps_card_in_place(admin, fake_github):
    fake_github.add_pull("org", "helios", 7)
    fake_github.add_pull("org", "web", 3, labels=["Aprobado"])
    fake_github.repo_labels[("org", "web")] = {"Aprobado"}
    card = await _card_in(admin, "Testing", [PR1, PR2, "https://example.com/no-es-pr"])

    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
    assert resp.status_code == 200, resp.text

    # helios no tenía el label: se creó en el repo y se puso en el PR.
    assert fake_github.repo_labels[("org", "helios")] == {"Aprobado"}
    assert {"name": "Aprobado"} in fake_github.pulls[("org", "helios", 7)]["labels"]
    # web ya lo tenía: no se vuelve a poner.
    assert not any(m == "POST" and "/org/web/" in p for m, p, _ in fake_github.calls)

    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["listName"] == "Testing"  # sigue siendo un PR: no se mueve
    # Los dos PRs quedan aprobados (uno ya lo estaba); el link que no es PR se ignora.
    assert f"Label **Aprobado** en: {PR1}, {PR2}" in detail["comments"][0]["body"]
    approved = next(a for a in detail["activity"] if a["action"] == "approved")
    assert approved["data"] == {"label": "Aprobado", "pulls": [PR1, PR2]}
    assert not any(a["action"] == "moved" for a in detail["activity"])


async def test_approve_from_pr_list_also_labels(admin, fake_github):
    fake_github.add_pull("org", "helios", 7)
    card = await _card_in(admin, "PR", [PR1])
    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
    assert resp.status_code == 200, resp.text
    assert fake_github.pulls[("org", "helios", 7)]["labels"] == [{"name": "Aprobado"}]
    assert resp.json()["data"]["listName"] == "PR"


async def test_approve_fails_if_label_cannot_be_set(admin, fake_github):
    fake_github.add_pull("org", "helios", 7)
    fake_github.fail[("POST", "/repos/org/helios/labels")] = (403, "Resource not accessible by integration")
    card = await _card_in(admin, "Testing", [PR1])

    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
    assert resp.status_code == 502
    assert "Resource not accessible" in resp.json()["detail"]
    # Nada escrito en la tarjeta: ni comentario ni movimiento.
    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["listName"] == "Testing" and detail["comments"] == []


async def test_approve_needs_an_open_pr(admin, fake_github):
    fake_github.add_pull("org", "helios", 7, merged=True, state="closed")
    sin_pr = await _card_in(admin, "Testing", [])
    merged = await _card_in(admin, "Testing", [PR1])
    for card in (sin_pr, merged):
        resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
        assert resp.status_code == 400 and "No hay PRs abiertos" in resp.json()["detail"]
        assert (await admin.get(f"/api/cards/{card['id']}")).json()["data"]["comments"] == []


async def test_approve_without_user_github_token(admin, fake_github):
    await admin.delete("/api/auth/me/github-token")
    card = await _card_in(admin, "Testing", [PR1])
    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
    assert resp.status_code == 400 and "token personal de GitHub" in resp.json()["detail"]
    assert fake_github.calls == []


async def test_reject_returns_prs_to_revisado(admin, fake_github):
    fake_github.add_pull("org", "helios", 7, labels=["Aprobado", "Otro"])
    fake_github.add_pull("org", "web", 3, labels=["Aprobado"], merged=True, state="closed")
    card = await _card_in(admin, "Testing", [PR1, PR2])
    pr_list = await _list_id(admin, "PR")

    # Sin notas o sin lista no se escribe nada en GitHub.
    for body in ({"approved": False, "listId": pr_list}, {"approved": False, "notes": "falla"}):
        resp = await admin.post(f"/api/cards/{card['id']}/qa", json=body)
        assert resp.status_code == 400
    assert fake_github.calls == []

    resp = await admin.post(f"/api/cards/{card['id']}/qa",
                            json={"approved": False, "listId": pr_list, "notes": "Falla el login"})
    assert resp.status_code == 200, resp.text
    pr = fake_github.pulls[("org", "helios", 7)]
    # Abierto, sin "Aprobado" y con "Revisado" (que se crea en el repo si no existía).
    assert pr["state"] == "open" and pr["labels"] == [{"name": "Otro"}, {"name": "Revisado"}]
    assert fake_github.repo_labels[("org", "helios")] == {"Revisado"}
    assert fake_github.pulls[("org", "web", 3)]["labels"] == [{"name": "Aprobado"}]  # el mergeado no se toca
    assert not any(m in ("PATCH", "PUT") for m, _, _ in fake_github.calls)  # nunca cierra ni mergea

    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["listName"] == "PR"
    assert detail["comments"][0]["body"] == (
        f"❌ **QA rechazado**\n\nFalla el login\n\nPRs devueltos a **Revisado**: {PR1}")
    rejected = next(a for a in detail["activity"] if a["action"] == "rejected")
    assert rejected["data"] == {"label": "Revisado", "pulls": [PR1]}

    # Un PR sin ningún label también queda en "Revisado"; uno que ya lo tiene no se vuelve a etiquetar.
    fake_github.add_pull("org", "web", 4, labels=["Revisado"])
    fake_github.calls.clear()
    other = await _card_in(admin, "PR", ["https://github.com/org/web/pull/4"])
    resp = await admin.post(f"/api/cards/{other['id']}/qa",
                            json={"approved": False, "listId": pr_list, "notes": "otra vez"})
    assert resp.status_code == 200, resp.text
    assert not any(m in ("POST", "DELETE") for m, _, _ in fake_github.calls)


async def test_reject_fails_if_label_cannot_be_changed(admin, fake_github):
    fake_github.add_pull("org", "helios", 7, labels=["Aprobado"])
    fake_github.fail[("DELETE", "/repos/org/helios/issues/7/labels/Aprobado")] = (
        403, "Resource not accessible by integration")
    card = await _card_in(admin, "Testing", [PR1])
    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={
        "approved": False, "listId": await _list_id(admin, "PR"), "notes": "falla"})
    assert resp.status_code == 502 and 'devolver el PR a "Revisado"' in resp.json()["detail"]
    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["listName"] == "Testing" and detail["comments"] == []


async def test_approve_removes_pending_label(admin, fake_github):
    fake_github.add_pull("org", "helios", 7, labels=["Pendiente"])
    card = await _card_in(admin, "Testing", [PR1])
    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
    assert resp.status_code == 200, resp.text
    assert fake_github.pulls[("org", "helios", 7)]["labels"] == [{"name": "Aprobado"}]


@pytest.mark.parametrize("label", ["Revisado", "Waiting"])
async def test_approve_rejected_if_code_review_pending(admin, fake_github, label):
    # Un PR listo y otro con la revisión de código pendiente: no se etiqueta ninguno.
    fake_github.add_pull("org", "helios", 7, labels=["Pendiente"])
    fake_github.add_pull("org", "web", 3, labels=[label])
    card = await _card_in(admin, "Testing", [PR1, PR2])

    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": True})
    assert resp.status_code == 409
    assert f"{PR2} ({label})" in resp.json()["detail"]
    assert not any(m in ("POST", "DELETE") for m, _, _ in fake_github.calls)
    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["comments"] == []
