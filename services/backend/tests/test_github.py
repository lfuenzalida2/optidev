import asyncio

from src.config.settings import settings
from src.services import github_service


def _pr(number, **extra):
    return {
        "number": number, "title": f"PR {number}", "url": f"https://github.com/org/helios/pull/{number}",
        "isDraft": False, "createdAt": "2026-01-01T10:00:00Z", "headRefName": "feat/x", "baseRefName": "staging",
        "author": {"login": "fmaturana", "avatarUrl": "https://a", "url": "https://github.com/fmaturana"},
        "labels": {"nodes": [{"name": "bug", "color": "d73a4a"}]},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": None}}]},
        **extra,
    }


def test_parse_github_url():
    assert github_service.parse_github_url("https://github.com/org/repo") == ("org", "repo")
    assert github_service.parse_github_url("https://github.com/org/repo.git/") == ("org", "repo")
    assert github_service.parse_github_url("git@github.com:org/my.repo.git") == ("org", "my.repo")
    assert github_service.parse_github_url("https://gitlab.com/org/repo") is None
    assert github_service.parse_github_url(None) is None


async def test_requires_token(admin, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", None)
    resp = await admin.get("/api/github/pulls")
    assert resp.status_code == 503
    assert "GITHUB_TOKEN" in resp.json()["detail"]


async def test_open_pull_requests(admin, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    await admin.post("/api/repositories", json={"key": "helios", "name": "Helios",
                                                "githubUrl": "https://github.com/org/helios"})
    await admin.post("/api/repositories", json={"key": "gone", "name": "Gone",
                                                "githubUrl": "https://github.com/org/gone"})
    await admin.post("/api/repositories", json={"key": "local", "name": "Sin GitHub"})

    queries = []
    checks = {"state": "FAILURE", "contexts": {"nodes": [
        {"__typename": "CheckRun", "name": "backend", "status": "COMPLETED", "conclusion": "FAILURE",
         "detailsUrl": "https://ci/1"},
        {"__typename": "CheckRun", "name": "frontend", "status": "IN_PROGRESS", "conclusion": None,
         "detailsUrl": "https://ci/2"},
        {"__typename": "StatusContext", "context": "deploy", "state": "SUCCESS", "targetUrl": None},
    ]}}

    async def fake_query(query):
        queries.append(query)
        # Los repos se ordenan por key: gone=r0, helios=r1, local=r2 (sin URL, no se consulta).
        return {
            "data": {
                "r0": None,
                "r1": {"pullRequests": {"totalCount": 2, "nodes": [
                    _pr(7, commits={"nodes": [{"commit": {"statusCheckRollup": checks}}]}),
                    _pr(3, author=None),
                ]}},
            },
            "errors": [{"path": ["r0"], "message": "Could not resolve to a Repository"}],
        }

    monkeypatch.setattr(github_service, "_query", fake_query)
    github_service._cache.update(at=0.0, key=None, data=None)

    resp = await admin.get("/api/github/pulls")
    assert resp.status_code == 200, resp.text
    repos = {r["key"]: r for r in resp.json()["data"]["repositories"]}
    assert 'owner: "org", name: "helios"' in queries[0] and "local" not in queries[0]

    assert repos["gone"]["error"] == "Could not resolve to a Repository"
    assert repos["local"]["error"] == "Sin URL de GitHub válida"
    helios = repos["helios"]
    assert helios["error"] is None and helios["totalCount"] == 2
    first, second = helios["pulls"]
    assert (first["number"], first["headRef"], first["baseRef"]) == (7, "feat/x", "staging")
    assert first["labels"] == [{"name": "bug", "color": "#d73a4a"}]
    assert first["openDays"] > 0
    assert first["checks"]["state"] == "failure"
    assert [c["state"] for c in first["checks"]["items"]] == ["failure", "pending", "success"]
    assert second["checks"] is None
    assert second["author"]["login"] == "ghost"

    # Segunda llamada: sale del caché; con refresh=true vuelve a consultar.
    await admin.get("/api/github/pulls")
    assert len(queries) == 1
    await admin.get("/api/github/pulls", params={"refresh": True})
    assert len(queries) == 2


async def test_pulls_served_from_cache_until_refreshed(admin, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    await admin.post("/api/repositories", json={"key": "helios", "name": "Helios",
                                                "githubUrl": "https://github.com/org/helios"})
    calls = []

    async def fake_query(query):
        calls.append(query)
        return {"data": {"r0": {"pullRequests": {"totalCount": 1, "nodes": [_pr(len(calls))]}}}}

    monkeypatch.setattr(github_service, "_query", fake_query)
    github_service._cache.update(at=0.0, key=None, data=None)

    first = await admin.get("/api/github/pulls")
    # Aunque haya pasado más que GITHUB_CACHE_SECONDS, se sirve el caché sin consultar.
    github_service._cache["at"] -= 10_000
    second = await admin.get("/api/github/pulls")
    assert len(calls) == 1
    assert second.json()["data"] == first.json()["data"]

    forced = await admin.get("/api/github/pulls?refresh=true")
    assert len(calls) == 2
    assert forced.json()["data"]["repositories"][0]["pulls"][0]["number"] == 2


async def test_refresh_loop_updates_cache_and_survives_errors(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(settings, "GITHUB_PULLS_REFRESH_SECONDS", 0)
    calls = []

    async def fake_open_pull_requests(db, force=False):
        calls.append(force)
        if len(calls) == 1:
            raise github_service.ServiceError("GitHub caído", 502)

    monkeypatch.setattr(github_service, "open_pull_requests", fake_open_pull_requests)
    task = asyncio.create_task(github_service.refresh_pulls_loop())
    for _ in range(100):
        if len(calls) >= 2:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    assert calls[:2] == [True, True]


async def _setup_pull(admin, monkeypatch, author="fmaturana"):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    await admin.post("/api/repositories", json={"key": "helios", "name": "Helios",
                                                "githubUrl": "https://github.com/org/helios"})

    async def fake_query(query):
        return {"data": {"r0": {"pullRequests": {"totalCount": 1, "nodes": [
            _pr(7, author={"login": author, "avatarUrl": None, "url": None})]}}}}

    async def fake_rest(method, path, **kwargs):
        if path.startswith("/repos/org/helios/issues/7/comments"):
            return 200, [{"user": {"login": "rev"}, "body": "Ojo con esto", "created_at": "2026-01-02T10:00:00Z",
                          "html_url": "https://c/1"}]
        return 200, [{"user": {"login": "rev"}, "body": "Renombrar", "created_at": "2026-01-01T12:00:00Z",
                      "html_url": "https://c/2", "path": "src/a.py"}]

    monkeypatch.setattr(github_service, "_query", fake_query)
    monkeypatch.setattr(github_service, "rest", fake_rest)
    github_service._cache.update(at=0.0, key=None, data=None)


async def test_card_from_pull_request(admin, monkeypatch):
    from src.config.database import AsyncSessionLocal
    from src.models.user import User

    me = (await admin.get("/api/auth/me")).json()["data"]
    await _setup_pull(admin, monkeypatch)
    # El autor del PR se reconoce por su github_login: se vincula al usuario actual.
    async with AsyncSessionLocal() as db:
        (await db.get(User, me["id"])).github_login = "FMaturana"
        await db.commit()

    listed = (await admin.get("/api/github/pulls")).json()["data"]["repositories"][0]["pulls"]
    assert listed[0]["cardId"] is None

    resp = await admin.post("/api/github/pulls/card", json={"repoKey": "helios", "number": 7})
    assert resp.status_code == 201, resp.text
    card = resp.json()["data"]
    assert card["title"] == card["description"] == "PR 7"
    listed = (await admin.get("/api/github/pulls")).json()["data"]["repositories"][0]["pulls"]
    assert listed[0]["cardId"] == card["id"]
    assert card["prUrls"] == ["https://github.com/org/helios/pull/7"]
    assert card["assignee"]["id"] == me["id"]

    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    bodies = [c["body"] for c in detail["comments"]]
    assert len(bodies) == 2 and "src/a.py" in bodies[0] and "Ojo con esto" in bodies[1]

    again = await admin.post("/api/github/pulls/card", json={"repoKey": "helios", "number": 7})
    assert again.status_code == 409
    missing = await admin.post("/api/github/pulls/card", json={"repoKey": "helios", "number": 99})
    assert missing.status_code == 404


async def test_card_from_pull_request_unknown_author(admin, monkeypatch):
    await _setup_pull(admin, monkeypatch, author="nadie")
    resp = await admin.post("/api/github/pulls/card", json={"repoKey": "helios", "number": 7})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["assignee"] is None
