from src.config.settings import settings
from src.services import github_service
from tests.test_board import _list_id, _new_card

PR_URL = "https://github.com/org/helios/pull/7"


def _detail(**extra):
    return {
        "number": 7, "title": "Optimizar deploy", "url": PR_URL, "state": "OPEN", "isDraft": False,
        "headRefName": "feat/deploy", "baseRefName": "staging", "mergeable": "CONFLICTING",
        "reviewDecision": None, "additions": 10, "deletions": 2, "changedFiles": 3,
        "updatedAt": "2026-01-02T10:00:00Z", "author": None,
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]},
        **extra,
    }


def test_parse_pr_url():
    assert github_service.parse_pr_url(PR_URL) == ("org", "helios", 7)
    assert github_service.parse_pr_url(PR_URL + "/files") == ("org", "helios", 7)
    assert github_service.parse_pr_url("https://github.com/org/helios") is None


async def test_testing_queue(admin, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    pr_list, testing, pending = [await _list_id(admin, n) for n in ("PR", "Testing", "Pendiente")]
    in_pr = await _new_card(admin, pr_list, title="con PR")
    await admin.patch(f"/api/cards/{in_pr['id']}", json={"prUrls": [PR_URL]})
    in_pr2 = await _new_card(admin, pr_list, title="links raros")
    await admin.patch(f"/api/cards/{in_pr2['id']}",
                      json={"prUrls": ["https://github.com/org/gone/pull/1", "https://example.com/x"]})
    await _new_card(admin, testing, title="en testing")
    await _new_card(admin, pending, title="pendiente")

    queries = []

    async def fake_query(query):
        queries.append(query)
        # URLs ordenadas: example.com = p0 (no se consulta), gone/pull/1 = p1, helios/pull/7 = p2.
        return {"data": {"p1": None, "p2": {"pullRequest": _detail()}},
                "errors": [{"path": ["p1"], "message": "Could not resolve to a Repository"}]}

    monkeypatch.setattr(github_service, "_query", fake_query)
    github_service._detail_cache.update(at=0.0, key=None, data=None)

    resp = await admin.get("/api/github/testing")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["listIds"] == [pr_list, testing]
    assert [c["title"] for c in data["cards"]] == ["con PR", "links raros", "en testing"]
    assert 'pullRequest(number: 7)' in queries[0] and "example.com" not in queries[0]

    pull = data["cards"][0]["pulls"][0]
    assert (pull["state"], pull["mergeable"], pull["headRef"]) == ("open", "conflicting", "feat/deploy")
    assert pull["checks"]["state"] == "success" and pull["author"]["login"] == "ghost"
    assert [p["error"] for p in data["cards"][1]["pulls"]] == [
        "Could not resolve to a Repository", "No es un link de PR de GitHub"]
    assert data["cards"][2]["pulls"] == []

    # Se puede elegir otras listas; el detalle sale del caché.
    resp = await admin.get("/api/github/testing", params={"listIds": [pending]})
    assert [c["title"] for c in resp.json()["data"]["cards"]] == ["pendiente"]
    await admin.get("/api/github/testing")
    assert len(queries) == 1


async def test_testing_queue_without_github(admin, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    card = await _new_card(admin, await _list_id(admin, "PR"))
    await admin.patch(f"/api/cards/{card['id']}", json={"prUrls": [PR_URL]})
    data = (await admin.get("/api/github/testing")).json()["data"]
    assert "GITHUB_TOKEN" in data["githubError"]
    assert data["cards"][0]["pulls"] == [{"url": PR_URL, "error": None}]


async def test_qa_review(admin):
    pr_list, in_progress = [await _list_id(admin, n) for n in ("PR", "En proceso")]
    card = await _new_card(admin, pr_list)

    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": False, "listId": in_progress})
    assert resp.status_code == 400 and "qué falló" in resp.json()["detail"]
    resp = await admin.post(f"/api/cards/{card['id']}/qa", json={"approved": False, "listId": 9999, "notes": "x"})
    assert resp.status_code == 404
    # Ninguna de las dos dejó comentario.
    assert (await admin.get(f"/api/cards/{card['id']}")).json()["data"]["comments"] == []

    # Rechazar una tarjeta sin PRs solo comenta y la mueve.
    resp = await admin.post(f"/api/cards/{card['id']}/qa",
                            json={"approved": False, "listId": in_progress, "notes": "Falla el login"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["listId"] == in_progress
    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert [c["body"] for c in detail["comments"]] == ["❌ **QA rechazado**\n\nFalla el login"]
