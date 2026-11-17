"""Botón "Mergear PR": rebasea sobre la rama a la que apunta cada PR y mergea los PRs aprobados de una tarjeta.

Replica `scripts/rebase-approved-prs.sh` de optidev-helios, pero para los PRs
registrados en una tarjeta en vez de todos los del repo. Por cada PR:

  1. Tiene que estar abierto, no ser borrador, no venir de un fork y tener el
     label "Aprobado"; si no, se saltea (no es un error de la tarjeta).
  2. Rama destino: la rama a la que apunta el PR (`base`), sea `staging`, una
     rama de feature o la que sea.
  3. En un directorio temporal propio (nunca en un clon que use otra cosa):
     fetch de la rama destino y la del PR. Si el PR ya contiene la punta del
     destino no se rebasea; si no, `git rebase` en HEAD detached. Con
     conflictos: `git rebase --abort`, no se pushea nada y se reporta.
  4. Push con `--force-with-lease=refs/heads/<rama>:<sha que se bajó>`: falla
     si alguien pusheó a la rama mientras tanto. Nunca force a secas.
  5. Merge por la API (`merge_method=rebase`, con el sha esperado, como
     `gh pr merge --rebase --match-head-commit`). GitHub tarda unos segundos en
     recalcular la mergeabilidad tras el push: se reintenta (5 × 3 s).

Igual que el script: no espera a que terminen los checks de CI (la protección
de rama de GitHub, si existe, rechaza el merge y se reporta), no quita el
label "Aprobado" y no borra la rama del PR. La tarjeta pasa a Hecho solo si todos
sus PRs quedaron mergeados (ver `board_service.record_merge`).

"Rebase" es lo mismo, también sobre la rama a la que apunta cada PR, sin el label
"Aprobado" como requisito y sin el paso 5: deja la rama del PR rebaseada y
pusheada (útil para resolver "Conflictos" o recargar en la VM con la base al
día) y no mueve la tarjeta.

Dos merges del mismo repo no corren a la vez (un lock por repo): el segundo
espera y rebasea sobre el destino ya actualizado. El lock es de proceso;
alcanza porque el backend corre un solo proceso (ver README).

Credenciales: los PRs y las ramas se leen con GITHUB_TOKEN; el rebase, el push y
el merge van con el token personal de quien aprieta el botón (los commits y el
merge quedan a su nombre). En git el token va por variables de entorno (no en la
URL ni en la línea de comandos), sin la config global de git. Necesita escritura
de Contents y Pull requests en los repos.
"""

import asyncio
import re
import shutil
import tempfile

from sqlalchemy.ext.asyncio import AsyncSession

from src.services import approval_service, board_service, github_service
from src.services.auth_service import Actor
from src.services.errors import ServiceError
from src.services.preview_service import _git_env

MERGE_ATTEMPTS = 5
MERGE_RETRY_SECONDS = 3.0
GIT_TIMEOUT_SECONDS = 300

_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_locks: dict[str, asyncio.Lock] = {}


class _GitError(Exception):
    def __init__(self, args: tuple, code: int, output: str):
        super().__init__(f"git {args[0]} falló (código {code})")
        self.output = output


def _lock(slug: str) -> asyncio.Lock:
    return _locks.setdefault(slug.lower(), asyncio.Lock())


def remote_url(owner: str, name: str) -> str:
    """URL del remoto (los tests la reemplazan por un repo local)."""
    return f"https://github.com/{owner}/{name}.git"


def _valid_branch(branch: str | None) -> bool:
    return bool(branch and _BRANCH.match(branch) and ".." not in branch
                and not branch.startswith(("-", "/")) and not branch.endswith((".lock", "/")))


async def _git(cwd: str, env: dict, *args: str, check: bool = True) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("git", *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise _GitError(args, -1, f"git {args[0]} no terminó en {GIT_TIMEOUT_SECONDS} s")
    text = out.decode(errors="replace")
    if check and proc.returncode != 0:
        raise _GitError(args, proc.returncode, text)
    return proc.returncode, text


def _tail(text: str, lines: int = 8) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


async def _rebase_and_push(owner: str, name: str, branch: str, base: str, actor: Actor, token: str) -> dict:
    """Rebasea `branch` sobre `base` en un directorio temporal y la pushea con
    lease. Devuelve {status, sha, rebased, message}; status = ok | conflict | failed."""
    env = _git_env(token)
    # Solo el committer: el rebase conserva el autor de cada commit.
    env.update(GIT_COMMITTER_NAME=actor.user.name or actor.user.username,
               GIT_COMMITTER_EMAIL=actor.user.email or f"{actor.user.username}@optidev")
    workdir = tempfile.mkdtemp(prefix="optidev-merge-")
    try:
        await _git(workdir, env, "init", "-q")
        await _git(workdir, env, "remote", "add", "origin", remote_url(owner, name))
        await _git(workdir, env, "fetch", "--no-tags", "-q", "origin",
                   f"+refs/heads/{base}:refs/remotes/origin/{base}",
                   f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        head_sha = (await _git(workdir, env, "rev-parse", f"refs/remotes/origin/{branch}"))[1].strip()
        up_to_date, _ = await _git(workdir, env, "merge-base", "--is-ancestor", f"refs/remotes/origin/{base}",
                                   head_sha, check=False)
        if up_to_date == 0:
            return {"status": "ok", "sha": head_sha, "rebased": False, "message": f"ya estaba al día con {base}"}

        await _git(workdir, env, "checkout", "-q", "--detach", head_sha)
        code, out = await _git(workdir, env, "rebase", f"refs/remotes/origin/{base}", check=False)
        if code != 0:
            _, files = await _git(workdir, env, "diff", "--name-only", "--diff-filter=U", check=False)
            await _git(workdir, env, "rebase", "--abort", check=False)
            conflicted = [f for f in files.split() if f]
            detail = f" en {', '.join(conflicted[:10])}" if conflicted else f": {_tail(out, 3)}"
            return {"status": "conflict", "sha": head_sha, "rebased": False,
                    "message": f"conflictos al rebasear sobre {base}{detail}. Rebase abortado, no se pusheó nada"}

        new_sha = (await _git(workdir, env, "rev-parse", "HEAD"))[1].strip()
        code, out = await _git(workdir, env, "push", "-q", f"--force-with-lease=refs/heads/{branch}:{head_sha}",
                               "origin", f"HEAD:refs/heads/{branch}", check=False)
        if code != 0:
            return {"status": "failed", "sha": head_sha, "rebased": False,
                    "message": f"push rechazado (¿la rama cambió en GitHub mientras tanto?): {_tail(out, 3)}"}
        return {"status": "ok", "sha": new_sha, "rebased": True, "message": f"rebaseado sobre {base}"}
    except _GitError as exc:
        return {"status": "failed", "sha": None, "rebased": False, "message": f"{exc}: {_tail(exc.output, 3)}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _merge(owner: str, name: str, number: int, sha: str, token: str) -> str | None:
    """Merge por rebase con el sha esperado. None = mergeado; si no, el motivo."""
    reason = None
    for attempt in range(MERGE_ATTEMPTS):
        status, body = await github_service.rest(
            "PUT", f"/repos/{owner}/{name}/pulls/{number}/merge",
            json={"merge_method": "rebase", "sha": sha}, allow=(405, 409, 422), token=token,
        )
        if status == 200 and (body or {}).get("merged", True):
            return None
        reason = (body or {}).get("message") or f"GitHub respondió {status}"
        # 409 = la rama cambió (sha distinto): reintentar no sirve.
        if status == 409:
            break
        if attempt < MERGE_ATTEMPTS - 1:
            await asyncio.sleep(MERGE_RETRY_SECONDS)
    return reason


async def _process(url: str, owner: str, name: str, number: int, actor: Actor, token: str,
                   rebase_only: bool = False) -> dict:
    item = {"url": url, "repository": f"{owner}/{name}", "number": number, "branch": None, "base": None,
            "status": "failed", "rebased": False, "message": None}
    _, pr = await github_service.rest("GET", f"/repos/{owner}/{name}/pulls/{number}")
    item["branch"], item["base"] = pr["head"]["ref"], pr["base"]["ref"]
    head_repo = (pr["head"].get("repo") or {}).get("full_name") or ""
    if pr.get("merged_at"):
        return {**item, "status": "merged", "message": "ya estaba mergeado"}
    if pr.get("state") != "open":
        return {**item, "status": "skipped", "message": "está cerrado"}
    if not rebase_only and not approval_service.has_approved_label(pr.get("labels")):
        return {**item, "status": "skipped",
                "message": f'no tiene el label "{approval_service.APPROVED_LABEL}" (aprobalo en QA desde Testing)'}
    if pr.get("draft"):
        return {**item, "status": "skipped", "message": "es un borrador"}
    if head_repo.lower() != pr["base"]["repo"]["full_name"].lower():
        return {**item, "status": "skipped", "message": "viene de un fork"}
    for ref in (item["branch"], item["base"]):
        if not _valid_branch(ref):
            return {**item, "message": f"nombre de rama no soportado: {ref}"}

    async with _lock(f"{owner}/{name}"):
        result = await _rebase_and_push(owner, name, item["branch"], item["base"], actor, token)
        item["rebased"] = result["rebased"]
        if result["status"] != "ok":
            return {**item, "status": result["status"], "message": result["message"]}
        if rebase_only:
            return {**item, "status": "rebased", "message": result["message"]}
        reason = await _merge(owner, name, number, result["sha"], token)
        if reason:
            done = "la rama quedó rebaseada y pusheada" if result["rebased"] else "la rama no se tocó"
            return {**item, "message": f"GitHub no lo mergeó ({done}): {reason}"}
        return {**item, "status": "merged", "message": f"{result['message']} y mergeado en {item['base']}"}


async def _process_card(db: AsyncSession, actor: Actor, card_id: int, rebase_only: bool) -> list[dict]:
    """Procesa cada PR de GitHub de la tarjeta y devuelve el resultado por PR.

    Un PR que falla (conflicto, push rechazado, merge rechazado) no corta los
    demás.
    """
    card = await board_service.get_card(db, card_id)
    if card.archived_at:
        raise ServiceError(f"La tarjeta #{card_id} está archivada")
    pulls = approval_service.github_pull_urls(card.pr_urls or [])
    if not pulls:
        raise ServiceError("La tarjeta no tiene PRs de GitHub registrados")
    token = github_service.user_token(actor.user)

    results = []
    for url, (owner, name, number) in pulls:
        try:
            results.append(await _process(url, owner, name, number, actor, token, rebase_only))
        except ServiceError as exc:
            results.append({"url": url, "repository": f"{owner}/{name}", "number": number, "branch": None,
                            "base": None, "status": "failed", "rebased": False, "message": exc.message})
    github_service.invalidate_cache()
    return results


async def merge_card(db: AsyncSession, actor: Actor, card_id: int) -> dict:
    """"Mergear PR": rebasea y mergea los PRs aprobados. El resultado queda como
    comentario y en el historial de la tarjeta."""
    results = await _process_card(db, actor, card_id, rebase_only=False)
    card_data, moved = await board_service.record_merge(db, actor, card_id, results)
    return {"results": results, "card": card_data, "movedToDone": moved}


async def rebase_card(db: AsyncSession, actor: Actor, card_id: int) -> dict:
    """"Rebase": rebasea sobre su rama base los PRs abiertos de la tarjeta, aprobados o
    no, sin mergearlos ni mover la tarjeta."""
    results = await _process_card(db, actor, card_id, rebase_only=True)
    card_data = await board_service.record_rebase(db, actor, card_id, results)
    return {"results": results, "card": card_data, "movedToDone": False}
