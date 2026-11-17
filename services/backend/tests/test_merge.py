"""Mergear PR: GitHub (API) mockeado, git real contra un repo bare local."""

import asyncio
import os
import subprocess

import pytest

from src.config.settings import settings
from src.services import github_service, merge_service
from tests.test_approval import FakeGithub
from tests.conftest import USER_TOKEN
from tests.test_board import _list_id, _new_card

PR1 = "https://github.com/org/helios/pull/7"
PR2 = "https://github.com/org/web/pull/3"
_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@x", "GIT_COMMITTER_NAME": "Dev",
        "GIT_COMMITTER_EMAIL": "dev@x"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=_ENV, check=True, capture_output=True, text=True).stdout.strip()


def commit(work, path, content, msg):
    with open(os.path.join(work, path), "w") as f:
        f.write(content)
    git(work, "add", path)
    git(work, "commit", "-q", "-m", msg)


class FakeMergeGithub(FakeGithub):
    def __init__(self, origins):
        super().__init__()
        self.origins = origins  # "owner/repo" -> ruta del bare
        self.merges = []
        self.merge_responses = []  # status a devolver antes del 200 (p. ej. 405 mientras recalcula)

    def _handle(self, method, path, json):
        parts = path.strip("/").split("/")
        slug = f"{parts[1]}/{parts[2]}"
        if (method, path) not in self.fail and parts[3] == "branches":
            exists = parts[4] in git(self.origins[slug], "branch", "--format=%(refname:short)").split()
            return (200, {"name": parts[4]}) if exists else (404, {"message": "Branch not found"})
        if (method, path) not in self.fail and parts[3] == "pulls" and len(parts) == 6 and parts[5] == "merge":
            if self.merge_responses:
                return self.merge_responses.pop(0), {"message": "Pull Request is not mergeable"}
            pr = self.pulls[(parts[1], parts[2], int(parts[4]))]
            bare = self.origins[slug]
            head = git(bare, "rev-parse", f"refs/heads/{pr['head']['ref']}")
            if head != json["sha"]:
                return 409, {"message": "Head branch was modified"}
            # Rebase merge = fast-forward de la base a la rama ya rebaseada.
            git(bare, "update-ref", f"refs/heads/{pr['base']['ref']}", head)
            self.merges.append((slug, int(parts[4]), json))
            pr.update(state="closed", merged_at="2026-10-27T00:00:00Z")
            return 200, {"merged": True}
        return super()._handle(method, path, json)


def make_origin(tmp_path, name, base="staging"):
    bare = str(tmp_path / f"{name}.git")
    work = str(tmp_path / f"{name}-work")
    git(str(tmp_path), "init", "-q", "--bare", bare)
    git(str(tmp_path), "init", "-q", "-b", base, work)
    git(work, "remote", "add", "origin", bare)
    commit(work, "a.txt", "uno\n", "base")
    git(work, "push", "-q", "origin", base)
    return bare, work


def add_pr(fake, owner, repo, number, branch, base="staging", labels=("Aprobado",), **extra):
    full = f"{owner}/{repo}"
    fake.add_pull(owner, repo, number, labels=labels, draft=False,
                  head={"ref": branch, "repo": {"full_name": full}},
                  base={"ref": base, "repo": {"full_name": full, "default_branch": "master"}}, **extra)


@pytest.fixture
def setup(monkeypatch, tmp_path, github_user):
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "ghp_test")
    monkeypatch.setattr(merge_service, "MERGE_RETRY_SECONDS", 0)
    origins = {}
    fake = FakeMergeGithub(origins)
    monkeypatch.setattr(github_service, "rest", fake.rest)
    # El rebase/push de git va con el token del usuario, nunca con el de la app.
    git_tokens = []
    real_env = merge_service._git_env
    monkeypatch.setattr(merge_service, "_git_env", lambda token: git_tokens.append(token) or real_env(token))
    fake.git_tokens = git_tokens
    monkeypatch.setattr(merge_service, "remote_url", lambda o, n: origins[f"{o}/{n}"])
    return fake, origins, tmp_path


async def _card(admin, urls):
    card = await _new_card(admin, await _list_id(admin, "Testing"))
    await admin.patch(f"/api/cards/{card['id']}", json={"prUrls": urls})
    return card


async def test_merge_success_rebases_and_merges(admin, setup):
    fake, origins, tmp = setup
    bare, work = make_origin(tmp, "helios")
    origins["org/helios"] = bare
    git(work, "checkout", "-q", "-b", "feat/x")
    commit(work, "b.txt", "feature\n", "feature")
    git(work, "push", "-q", "origin", "feat/x")
    git(work, "checkout", "-q", "staging")
    commit(work, "c.txt", "otro cambio\n", "staging avanza")
    git(work, "push", "-q", "origin", "staging")
    old_head = git(bare, "rev-parse", "feat/x")
    add_pr(fake, "org", "helios", 7, "feat/x")
    fake.merge_responses = [405]  # GitHub todavía recalculando: se reintenta
    card = await _card(admin, [PR1])

    resp = await admin.post(f"/api/cards/{card['id']}/merge")
    assert resp.status_code == 200, resp.text
    [result] = resp.json()["data"]["results"]
    assert result["status"] == "merged" and result["rebased"], result
    assert (result["branch"], result["base"]) == ("feat/x", "staging")

    new_head = git(bare, "rev-parse", "feat/x")
    assert new_head != old_head
    assert git(bare, "rev-parse", "staging") == new_head  # mergeado (ff)
    assert fake.git_tokens == [USER_TOKEN]
    assert all(call[0] == "PUT" for call in fake.calls if call[0] != "GET")
    assert git(bare, "log", "--format=%an", "-1", "feat/x") == "Dev"  # conserva el autor
    assert fake.merges[0][2] == {"merge_method": "rebase", "sha": new_head}
    # La rama del PR no se borra y el label no se quita (como el script de Helios).
    assert "feat/x" in git(bare, "branch", "--format=%(refname:short)").split()

    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["listName"] == "Hecho"  # todos los PRs mergeados
    assert resp.json()["data"]["movedToDone"] is True
    assert "1 de 1 mergeado" in detail["comments"][0]["body"]
    entry = next(a for a in detail["activity"] if a["action"] == "merge_attempted")
    assert entry["data"]["results"] == [{"url": PR1, "status": "merged"}]


async def test_merge_conflict_aborts_without_push(admin, setup):
    fake, origins, tmp = setup
    bare, work = make_origin(tmp, "helios")
    origins["org/helios"] = bare
    git(work, "checkout", "-q", "-b", "feat/x")
    commit(work, "a.txt", "versión feature\n", "feature")
    git(work, "push", "-q", "origin", "feat/x")
    git(work, "checkout", "-q", "staging")
    commit(work, "a.txt", "versión staging\n", "staging")
    git(work, "push", "-q", "origin", "staging")
    before = git(bare, "rev-parse", "feat/x"), git(bare, "rev-parse", "staging")
    add_pr(fake, "org", "helios", 7, "feat/x")
    card = await _card(admin, [PR1])

    resp = await admin.post(f"/api/cards/{card['id']}/merge")
    [result] = resp.json()["data"]["results"]
    assert result["status"] == "conflict" and "a.txt" in result["message"]
    assert (git(bare, "rev-parse", "feat/x"), git(bare, "rev-parse", "staging")) == before
    assert fake.merges == []
    body = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]["comments"][0]["body"]
    assert "0 de 1" in body and "conflictos" in body


async def test_merge_per_pr_results(admin, setup):
    """Varios repos: uno sin label (se saltea), uno al día sin staging (usa defaultBranch)."""
    fake, origins, tmp = setup
    # helios: PR sin el label Aprobado.
    origins["org/helios"], _ = make_origin(tmp, "helios")
    add_pr(fake, "org", "helios", 7, "feat/x", labels=())
    # web: no tiene staging; su defaultBranch en optidev es master.
    bare, work = make_origin(tmp, "web", base="master")
    origins["org/web"] = bare
    git(work, "checkout", "-q", "-b", "feat/y")
    commit(work, "y.txt", "y\n", "y")
    git(work, "push", "-q", "origin", "feat/y")
    add_pr(fake, "org", "web", 3, "feat/y", base="master")
    await admin.post("/api/repositories", json={"key": "web", "name": "Web", "defaultBranch": "master",
                                                "githubUrl": "https://github.com/org/web"})
    card = await _card(admin, [PR1, PR2])

    resp = await admin.post(f"/api/cards/{card['id']}/merge")
    results = {r["url"]: r for r in resp.json()["data"]["results"]}
    assert results[PR1]["status"] == "skipped" and "Aprobado" in results[PR1]["message"]
    assert results[PR2]["status"] == "merged" and not results[PR2]["rebased"]
    assert [(m[0], m[1]) for m in fake.merges] == [("org/web", 3)]
    # Un PR salteado: la tarjeta no pasa a Hecho.
    assert resp.json()["data"]["movedToDone"] is False
    assert resp.json()["data"]["card"]["listName"] != "Hecho"


async def test_merge_reports_push_race(admin, setup, monkeypatch):
    fake, origins, tmp = setup
    bare, work = make_origin(tmp, "helios")
    origins["org/helios"] = bare
    card = await _card(admin, [PR1])

    # Alguien pushea a la rama entre el fetch y el push: el lease lo rechaza.
    git(work, "checkout", "-q", "-b", "feat/z")
    commit(work, "z.txt", "z\n", "z")
    git(work, "push", "-q", "origin", "feat/z")
    git(work, "checkout", "-q", "staging")
    commit(work, "s.txt", "s\n", "s")
    git(work, "push", "-q", "origin", "staging")
    fake.pulls.clear()
    add_pr(fake, "org", "helios", 7, "feat/z")
    real_git = merge_service._git

    async def racing_git(cwd, env, *args, check=True):
        if args[0] == "push":
            git(work, "checkout", "-q", "feat/z")
            commit(work, "z2.txt", "z2\n", "otro push")
            git(work, "push", "-q", "origin", "feat/z")
        return await real_git(cwd, env, *args, check=check)

    monkeypatch.setattr(merge_service, "_git", racing_git)
    [result] = (await admin.post(f"/api/cards/{card['id']}/merge")).json()["data"]["results"]
    assert result["status"] == "failed" and "push rechazado" in result["message"]
    assert git(bare, "log", "-1", "--format=%s", "feat/z") == "otro push"
    assert fake.merges == []


@pytest.mark.parametrize("endpoint", ["merge", "rebase"])
async def test_targets_the_pr_base_branch(admin, setup, endpoint):
    """Rebasea sobre la rama a la que apunta el PR (no staging), aunque staging avance."""
    fake, origins, tmp = setup
    bare, work = make_origin(tmp, "helios")
    origins["org/helios"] = bare
    git(work, "checkout", "-q", "-b", "feat/base")
    commit(work, "base.txt", "base\n", "base")
    git(work, "push", "-q", "origin", "feat/base")
    git(work, "checkout", "-q", "-b", "feat/x")
    commit(work, "x.txt", "x\n", "x")
    git(work, "push", "-q", "origin", "feat/x")
    git(work, "checkout", "-q", "feat/base")
    commit(work, "base2.txt", "base2\n", "base avanza")
    git(work, "push", "-q", "origin", "feat/base")
    git(work, "checkout", "-q", "staging")
    commit(work, "s.txt", "s\n", "staging avanza")
    git(work, "push", "-q", "origin", "staging")
    add_pr(fake, "org", "helios", 7, "feat/x", base="feat/base")
    card = await _card(admin, [PR1])

    [result] = (await admin.post(f"/api/cards/{card['id']}/{endpoint}")).json()["data"]["results"]
    assert result["status"] == ("merged" if endpoint == "merge" else "rebased") and result["rebased"]
    assert git(bare, "merge-base", "--is-ancestor", "feat/base", "feat/x") == ""
    # staging avanzó pero no es la base del PR: no entra en la rama.
    assert git(bare, "ls-tree", "-r", "--name-only", "feat/x").split() == ["a.txt", "base.txt", "base2.txt", "x.txt"]


async def test_merge_requires_prs_and_token(admin, setup, monkeypatch):
    card = await _card(admin, [])
    resp = await admin.post(f"/api/cards/{card['id']}/merge")
    assert resp.status_code == 400 and "no tiene PRs" in resp.json()["detail"]
    await admin.delete("/api/auth/me/github-token")
    card = await _card(admin, [PR1])
    resp = await admin.post(f"/api/cards/{card['id']}/merge")
    assert resp.status_code == 400 and "token personal de GitHub" in resp.json()["detail"]


async def test_same_repo_merges_are_serialized(setup):
    lock = merge_service._lock("Org/Helios")
    assert lock is merge_service._lock("org/helios")
    order = []

    async def job(n):
        async with merge_service._lock("org/helios"):
            order.append(f"in{n}")
            await asyncio.sleep(0.01)
            order.append(f"out{n}")

    await asyncio.gather(job(1), job(2))
    assert order == ["in1", "out1", "in2", "out2"]


async def test_rebase_only_pushes_without_merging_or_approval(admin, setup):
    fake, origins, tmp = setup
    bare, work = make_origin(tmp, "helios")
    origins["org/helios"] = bare
    git(work, "checkout", "-q", "-b", "feat/x")
    commit(work, "b.txt", "feature\n", "feature")
    git(work, "push", "-q", "origin", "feat/x")
    git(work, "checkout", "-q", "staging")
    commit(work, "c.txt", "otro cambio\n", "staging avanza")
    git(work, "push", "-q", "origin", "staging")
    old_head, staging = git(bare, "rev-parse", "feat/x"), git(bare, "rev-parse", "staging")
    add_pr(fake, "org", "helios", 7, "feat/x", labels=())  # sin "Aprobado"
    card = await _card(admin, [PR1])

    resp = await admin.post(f"/api/cards/{card['id']}/rebase")
    assert resp.status_code == 200, resp.text
    [result] = resp.json()["data"]["results"]
    assert result["status"] == "rebased" and result["rebased"], result
    assert git(bare, "rev-parse", "feat/x") != old_head
    assert git(bare, "merge-base", "--is-ancestor", staging, "feat/x") == ""
    assert git(bare, "rev-parse", "staging") == staging and fake.merges == []
    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["listName"] == "Testing"
    assert "**Rebase**" in detail["comments"][0]["body"]
    assert any(a["action"] == "rebase_attempted" for a in detail["activity"])
