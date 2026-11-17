"""PRs abiertos de los repositorios del tablero, consultados en vivo a GitHub.

No se guarda nada en la BD: una sola consulta GraphQL trae los PRs de todos
los repos (un alias por repo) y el resultado queda en memoria. El dashboard
lee siempre de ese caché (carga rápida y sin gastar el rate limit por cada
persona con la vista abierta); se renueva solo cada GITHUB_PULLS_REFRESH_SECONDS
(`refresh_pulls_loop`), con el botón Actualizar (`force`) o cuando optidev
escribe en GitHub (`invalidate_cache`). Requiere GITHUB_TOKEN con lectura de
los repos (scope `repo` para privados).
"""

import asyncio
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import AsyncSessionLocal
from src.config.settings import settings
from src.models.repository import Repository
from src.models.user import User
from src.services.errors import ServiceError
from src.services.secret_box import decrypt_json

logger = logging.getLogger(__name__)

API_URL = "https://api.github.com"
GRAPHQL_URL = "https://api.github.com/graphql"
USER_URL = "https://api.github.com/user"
_GITHUB_REPO = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")
_GITHUB_PR = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

# Estado agregado de los checks (statusCheckRollup) → lo que muestra la UI.
CHECK_STATES = {"SUCCESS": "success", "FAILURE": "failure", "ERROR": "failure", "PENDING": "pending",
                "EXPECTED": "pending"}

_ROLLUP = """statusCheckRollup {
  state
  contexts(first: 30) { nodes {
    __typename
    ... on CheckRun { name status conclusion detailsUrl }
    ... on StatusContext { context state targetUrl }
  } }
}"""

_PR_FIELDS = """
  pullRequests(states: OPEN, first: 50, orderBy: {field: CREATED_AT, direction: DESC}) {
    totalCount
    nodes {
      number title url isDraft createdAt headRefName baseRefName
      author { login avatarUrl url }
      labels(first: 20) { nodes { name color } }
      commits(last: 1) { nodes { commit { %s } } }
    }
  }
""" % _ROLLUP

# Detalle de un PR puntual (cola de testing): además del estado de los checks
# trae si sigue abierto, si tiene conflictos y la decisión del review.
_PR_DETAIL_FIELDS = """
  number title url state isDraft headRefName baseRefName mergeable reviewDecision
  additions deletions changedFiles updatedAt
  author { login avatarUrl url }
  labels(first: 20) { nodes { name color } }
  commits(last: 1) { nodes { commit { %s } } }
""" % _ROLLUP

# Token temporal para una operación puntual (p. ej. arreglar la configuración de
# un repo con un token con más permisos que GITHUB_TOKEN). Vive solo en el
# contexto de esa petición: no se guarda ni se loguea.
_token_override: ContextVar[str | None] = ContextVar("github_token_override", default=None)


@contextmanager
def use_token(token: str):
    """Dentro del bloque, `rest()` usa `token` en vez de GITHUB_TOKEN."""
    marker = _token_override.set(token)
    try:
        yield
    finally:
        _token_override.reset(marker)


_cache: dict = {"at": 0.0, "key": None, "data": None}
_detail_cache: dict = {"at": 0.0, "key": None, "data": None}


def parse_github_url(url: str | None) -> tuple[str, str] | None:
    """`https://github.com/org/repo(.git)` o `git@github.com:org/repo.git` → (org, repo)."""
    match = _GITHUB_REPO.search((url or "").strip())
    return (match.group(1), match.group(2)) if match else None


def parse_pr_url(url: str | None) -> tuple[str, str, int] | None:
    """`https://github.com/org/repo/pull/12(/files…)` → (org, repo, 12)."""
    match = _GITHUB_PR.search((url or "").strip())
    return (match.group(1), match.group(2), int(match.group(3))) if match else None


def _check(node: dict) -> dict:
    if node.get("__typename") == "StatusContext":
        state = CHECK_STATES.get(node.get("state"), "pending")
        return {"name": node.get("context"), "state": state, "url": node.get("targetUrl")}
    if node.get("status") != "COMPLETED":
        state = "pending"
    elif node.get("conclusion") in ("SUCCESS", "NEUTRAL", "SKIPPED"):
        state = "success"
    else:
        state = "failure"
    return {"name": node.get("name"), "state": state, "url": node.get("detailsUrl")}


def _checks(pr: dict) -> dict | None:
    commit = (pr.get("commits", {}).get("nodes") or [{}])[0].get("commit") or {}
    rollup = commit.get("statusCheckRollup")
    # None = el repo no tiene GitHub Actions/checks para ese commit.
    return None if not rollup else {
        "state": CHECK_STATES.get(rollup["state"], "pending"),
        "items": [_check(n) for n in rollup["contexts"]["nodes"] if n],
    }


def _author(pr: dict) -> dict:
    # Autor borrado en GitHub = author null ("ghost").
    author = pr.get("author") or {}
    return {"login": author.get("login") or "ghost", "avatarUrl": author.get("avatarUrl"), "url": author.get("url")}


def _pull_dict(pr: dict, now: datetime) -> dict:
    created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
    return {
        "number": pr["number"],
        "title": pr["title"],
        "url": pr["url"],
        "isDraft": pr["isDraft"],
        "headRef": pr["headRefName"],
        "baseRef": pr["baseRefName"],
        "author": _author(pr),
        "labels": [{"name": lbl["name"], "color": f"#{lbl['color']}"} for lbl in pr["labels"]["nodes"]],
        "createdAt": created.isoformat(),
        "openDays": (now - created).days,
        "checks": _checks(pr),
    }


async def fetch_login(token: str) -> str:
    """Login de GitHub dueño del token; valida que el token sirva."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx2.AsyncClient(timeout=20) as client:
            resp = await client.get(USER_URL, headers=headers)
    except httpx2.HTTPError as exc:
        raise ServiceError(f"No se pudo consultar GitHub: {exc}", 502) from exc
    if resp.status_code == 401:
        raise ServiceError("GitHub rechazó el token (inválido o vencido)")
    if resp.status_code != 200:
        raise ServiceError(f"GitHub respondió {resp.status_code}", 502)
    return resp.json()["login"]


async def _query(query: str) -> dict:
    headers = {"Authorization": f"Bearer {settings.GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx2.AsyncClient(timeout=20) as client:
            resp = await client.post(GRAPHQL_URL, json={"query": query}, headers=headers)
    except httpx2.HTTPError as exc:
        raise ServiceError(f"No se pudo consultar GitHub: {exc}", 502) from exc
    if resp.status_code == 401:
        raise ServiceError("GITHUB_TOKEN inválido o vencido", 502)
    if resp.status_code != 200:
        raise ServiceError(f"GitHub respondió {resp.status_code}", 502)
    return resp.json()


async def open_pull_requests(db: AsyncSession, force: bool = False) -> dict:
    if not settings.GITHUB_TOKEN:
        raise ServiceError("Falta configurar GITHUB_TOKEN en el backend para ver el dashboard de GitHub", 503)

    repos = list((await db.execute(
        select(Repository).where(Repository.deleted_at.is_(None)).order_by(Repository.key)
    )).scalars())
    targets = [(f"r{i}", repo, parse_github_url(repo.github_url)) for i, repo in enumerate(repos)]

    cache_key = tuple((repo.id, gh) for _, repo, gh in targets)
    # Sin vencimiento: lo renueva `refresh_pulls_loop`. Si cambian los repos del
    # tablero la clave no coincide y se consulta en vivo.
    if not force and _cache["key"] == cache_key:
        return _cache["data"]

    aliases = [f'{alias}: repository(owner: "{gh[0]}", name: "{gh[1]}") {{ {_PR_FIELDS} }}'
               for alias, _, gh in targets if gh]
    payload = await _query("query { " + " ".join(aliases) + " }") if aliases else {"data": {}}
    found = payload.get("data") or {}
    # Un repo inaccesible (sin permiso, renombrado) viene como error con `path`
    # y data null para ese alias; el resto de la respuesta sirve igual.
    errors = {e["path"][0]: e.get("message") for e in payload.get("errors") or [] if e.get("path")}
    if not found and payload.get("errors") and not errors:
        raise ServiceError(f"Error de GitHub: {payload['errors'][0].get('message')}", 502)

    now = datetime.now(timezone.utc)
    result = []
    for alias, repo, gh in targets:
        item = {"key": repo.key, "name": repo.name, "color": repo.color, "githubUrl": repo.github_url,
                "error": None, "totalCount": 0, "pulls": []}
        if not gh:
            item["error"] = "Sin URL de GitHub válida"
        elif not found.get(alias):
            item["error"] = errors.get(alias) or "No se pudo leer el repositorio"
        else:
            prs = found[alias]["pullRequests"]
            item["totalCount"] = prs["totalCount"]
            item["pulls"] = [_pull_dict(pr, now) for pr in prs["nodes"]]
        result.append(item)

    data = {"repositories": result, "fetchedAt": now.isoformat()}
    _cache.update(at=time.monotonic(), key=cache_key, data=data)
    return data


async def refresh_pulls_loop() -> None:
    """Renueva el caché de PRs abiertos cada GITHUB_PULLS_REFRESH_SECONDS.

    Corre durante toda la vida del backend (ver `lifespan`). Un fallo de GitHub
    no corta el ciclo: el caché anterior se sigue sirviendo y se reintenta en
    el próximo turno. Sin GITHUB_TOKEN no consulta nada.
    """
    while True:
        if settings.GITHUB_TOKEN:
            try:
                async with AsyncSessionLocal() as db:
                    await open_pull_requests(db, force=True)
            except asyncio.CancelledError:
                raise
            except ServiceError as exc:
                logger.warning("No se pudo renovar el caché de PRs: %s", exc.message)
            except Exception:
                logger.exception("Error renovando el caché de PRs")
        await asyncio.sleep(settings.GITHUB_PULLS_REFRESH_SECONDS)


def _pr_detail_dict(url: str, pr: dict) -> dict:
    return {
        "url": url,
        "error": None,
        "number": pr["number"],
        "title": pr["title"],
        "githubUrl": pr["url"],
        # OPEN | MERGED | CLOSED
        "state": pr["state"].lower(),
        "isDraft": pr["isDraft"],
        "headRef": pr["headRefName"],
        "baseRef": pr["baseRefName"],
        # MERGEABLE | CONFLICTING | UNKNOWN (GitHub lo calcula en segundo plano)
        "mergeable": (pr.get("mergeable") or "UNKNOWN").lower(),
        # APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | None (sin reglas de review)
        "reviewDecision": (pr.get("reviewDecision") or "").lower() or None,
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changedFiles": pr.get("changedFiles"),
        "updatedAt": pr["updatedAt"].replace("Z", "+00:00"),
        "author": _author(pr),
        "labels": [{"name": lbl["name"], "color": f"#{lbl['color']}"}
                   for lbl in (pr.get("labels") or {}).get("nodes") or []],
        "checks": _checks(pr),
    }


def invalidate_cache() -> None:
    """Tras escribir en GitHub (labels, merges): la próxima lectura va en vivo."""
    _cache.update(at=0.0, key=None, data=None)
    _detail_cache.update(at=0.0, key=None, data=None)


def user_token(user: User) -> str:
    """Token personal de GitHub del usuario (descifrado). Toda acción que escribe
    en GitHub (labels, push, merge, PRs) va con este token, nunca con GITHUB_TOKEN."""
    if not user.github_token_enc:
        raise ServiceError("Cargá tu token personal de GitHub (perfil) para hacer cambios en GitHub: "
                           "las acciones sobre los repos se hacen a tu nombre", 400)
    return decrypt_json(user.github_token_enc)["token"]


async def rest(method: str, path: str, json: dict | None = None, allow: tuple[int, ...] = (),
               token: str | None = None) -> tuple[int, object]:
    """Llamada a la API REST de GitHub → (status, cuerpo JSON).

    Sin `token` es una lectura y va con GITHUB_TOKEN (el de la app); las
    escrituras pasan el token del usuario (`user_token`). Un status >= 400 lanza
    ServiceError salvo que esté en `allow` (p. ej. 404 para "no existe", 405
    para "todavía no se puede mergear").
    """
    explicit = bool(token)
    token = token or _token_override.get() or settings.GITHUB_TOKEN
    if not token:
        raise ServiceError("Falta configurar GITHUB_TOKEN en el backend", 503)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx2.AsyncClient(timeout=20) as client:
            resp = await client.request(method, f"{API_URL}{path}", json=json, headers=headers)
    except httpx2.HTTPError as exc:
        raise ServiceError(f"No se pudo consultar GitHub: {exc}", 502) from exc
    try:
        body = resp.json() if resp.content else None
    except ValueError:
        body = {"message": resp.text[:200]}
    if resp.status_code == 401:
        raise ServiceError("Tu token de GitHub es inválido o venció; cargalo de nuevo" if explicit
                           else "El token de GitHub es inválido o venció" if _token_override.get()
                           else "GITHUB_TOKEN inválido o vencido", 502)
    if resp.status_code >= 400 and resp.status_code not in allow:
        detail = body.get("message") if isinstance(body, dict) else None
        raise ServiceError(f"GitHub respondió {resp.status_code}: {detail or 'sin detalle'}", 502)
    return resp.status_code, body


async def pull_request_details(urls: list[str], force: bool = False) -> dict[str, dict]:
    """Detalle en vivo de los PRs indicados por URL, en una sola consulta GraphQL.

    Devuelve `{url: detalle}`; un link que no es de un PR de GitHub o un PR
    inaccesible viene con `error` y el resto de los campos ausentes.
    """
    if not settings.GITHUB_TOKEN:
        raise ServiceError("Falta configurar GITHUB_TOKEN en el backend para ver el estado de los PRs", 503)

    urls = sorted(set(urls))
    cache_key = tuple(urls)
    if (not force and _detail_cache["key"] == cache_key
            and time.monotonic() - _detail_cache["at"] < settings.GITHUB_CACHE_SECONDS):
        return _detail_cache["data"]

    targets = [(f"p{i}", url, parse_pr_url(url)) for i, url in enumerate(urls)]
    aliases = [f'{alias}: repository(owner: "{pr[0]}", name: "{pr[1]}") '
               f'{{ pullRequest(number: {pr[2]}) {{ {_PR_DETAIL_FIELDS} }} }}'
               for alias, _, pr in targets if pr]
    payload = await _query("query { " + " ".join(aliases) + " }") if aliases else {"data": {}}
    found = payload.get("data") or {}
    errors = {e["path"][0]: e.get("message") for e in payload.get("errors") or [] if e.get("path")}
    if not found and payload.get("errors") and not errors:
        raise ServiceError(f"Error de GitHub: {payload['errors'][0].get('message')}", 502)

    result = {}
    for alias, url, pr in targets:
        node = (found.get(alias) or {}).get("pullRequest")
        if not pr:
            result[url] = {"url": url, "error": "No es un link de PR de GitHub"}
        elif not node:
            result[url] = {"url": url, "error": errors.get(alias) or "No se pudo leer el PR"}
        else:
            result[url] = _pr_detail_dict(url, node)

    _detail_cache.update(at=time.monotonic(), key=cache_key, data=result)
    return result


async def pull_request_comments(owner: str, repo: str, number: int) -> list[dict]:
    """Comentarios de un PR (conversación + inline de review), del más viejo al más nuevo.

    Devuelve `{author, body, createdAt, url, path}`; `path` solo viene en los
    comentarios inline. Se piden hasta 100 de cada tipo.
    """
    _, issue_comments = await rest("GET", f"/repos/{owner}/{repo}/issues/{number}/comments?per_page=100")
    _, review_comments = await rest("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments?per_page=100")
    comments = [
        {
            "author": (c.get("user") or {}).get("login") or "ghost",
            "body": (c.get("body") or "").strip(),
            "createdAt": c["created_at"],
            "url": c.get("html_url"),
            "path": c.get("path"),
        }
        for c in [*issue_comments, *review_comments]
    ]
    return sorted((c for c in comments if c["body"]), key=lambda c: c["createdAt"])
