"""Pestaña Workflow: el repo de agentes (optidev-agents) navegable desde la
app y un chat con Claude que lo conoce y puede proponer cambios.

El repo es el del tablero con key WORKFLOW_REPO_KEY y se lee en vivo de GitHub
(GITHUB_TOKEN); no se guarda nada en la BD. El chat no tiene estado en el
servidor: el frontend manda el historial (solo texto) en cada mensaje. Dentro
de un mensaje Claude puede leer archivos y armar una propuesta de cambios, que
vuelve al frontend; recién cuando el usuario la aprueba se crean la rama, el
commit y el PR.

Cada mensaje trabaja sobre el commit que tenía la rama base al empezar
(`baseSha`) y el commit del PR se arma encima de ese mismo commit: si la rama
avanzó mientras tanto, el PR sigue mostrando solo lo propuesto y un eventual
conflicto aparece en GitHub en vez de pisar cambios ajenos.
"""

import difflib
import re
import time
import unicodedata
from datetime import datetime, timezone
from urllib.parse import quote

import anthropic
import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.models.repository import Repository
from src.services.auth_service import Actor
from src.services.errors import NotFoundError, ServiceError
from src.services.github_service import parse_github_url, user_token

API_URL = "https://api.github.com"
# Tope de lo que se muestra o se le pasa a Claude de un archivo.
MAX_FILE_BYTES = 200_000
# Llamadas a Claude por mensaje del usuario (cada lectura de archivos es una).
MAX_STEPS = 12
# Un workflow de Actions agregado en una rama corre con los secrets del repo:
# por eso .github/ no se puede tocar desde acá.
_FORBIDDEN_PATH = re.compile(r"^\.github(/|$)")

_head_cache: dict = {"at": 0.0, "key": None, "sha": None}
# El árbol de un commit no cambia: se cachea el del último commit pedido.
_tree_cache: dict = {"key": None, "data": None}

SYSTEM_PROMPT = """Eres el asistente de la pestaña Workflow de Optidev, el tablero de desarrollo de Optidev \
(empresa de conserjería remota para edificios y condominios).

Trabajas sobre el repositorio de agentes de Optidev: la documentación, skills, agentes, plugins de Claude \
Code y convenciones que usan las personas y las IAs del equipo. Respondes preguntas sobre su contenido, \
sugieres mejoras y, si el usuario quiere aplicar un cambio, lo propones para que lo apruebe.

Cómo trabajar:
- Abajo está la lista completa de archivos. Antes de afirmar algo sobre el contenido de un archivo, léelo con \
read_file; no lo supongas por el nombre. Cita las rutas que uses.
- Para proponer cambios llama a propose_changes con el contenido COMPLETO y final de cada archivo (no un diff \
ni un fragmento). Junta en una sola propuesta todo lo que corresponde a un mismo PR. Lee antes cada archivo que \
vayas a modificar, y conserva intacto todo lo que no pidas cambiar.
- Proponer no aplica nada: el usuario ve la propuesta con un botón para aprobarla, y solo entonces se abre el \
PR. No digas que el cambio está hecho.
- No se pueden modificar archivos dentro de .github/.
- Responde en español, en markdown, de forma breve y directa. Si algo que te piden es incorrecto o choca con \
lo que dice el repositorio, dilo."""

TOOLS = [
    {
        "name": "read_file",
        "description": "Lee el contenido de un archivo de texto del repositorio de agentes, en el commit actual.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Ruta desde la raíz, tal como aparece en la lista de archivos."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "propose_changes",
        "description": (
            "Propone un conjunto de cambios al repositorio, que el usuario puede aprobar para abrir un PR. "
            "Cada archivo lleva su contenido completo final; delete=true lo borra. Una nueva llamada reemplaza "
            "la propuesta anterior."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título del PR, corto (también es el mensaje del commit)."},
                "description": {"type": "string", "description": "Cuerpo del PR en markdown: qué cambia y por qué."},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string", "description": "Contenido completo final del archivo."},
                            "delete": {"type": "boolean"},
                        },
                        "required": ["path"],
                    },
                },
            },
            "required": ["title", "description", "files"],
        },
    },
]


async def _target(db: AsyncSession) -> tuple[Repository, str, str]:
    if not settings.GITHUB_TOKEN:
        raise ServiceError("Falta configurar GITHUB_TOKEN en el backend para ver el repositorio de agentes", 503)
    repo = (await db.execute(
        select(Repository).where(Repository.key == settings.WORKFLOW_REPO_KEY, Repository.deleted_at.is_(None))
    )).scalar_one_or_none()
    if not repo:
        raise ServiceError(f"No hay un repositorio con key '{settings.WORKFLOW_REPO_KEY}' en el tablero "
                           "(WORKFLOW_REPO_KEY)", 503)
    gh = parse_github_url(repo.github_url)
    if not gh:
        raise ServiceError(f"El repositorio '{repo.key}' no tiene una URL de GitHub válida", 503)
    return repo, gh[0], gh[1]


async def _rest(method: str, path: str, json: dict | None = None, raw: bool = False, token: str | None = None):
    """Llamada a la API REST de GitHub. `raw` devuelve los bytes del archivo
    (contents) en vez de JSON. Sin `token` es una lectura (GITHUB_TOKEN); las
    escrituras pasan el token del usuario."""
    headers = {
        "Authorization": f"Bearer {token or settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
    }
    try:
        async with httpx2.AsyncClient(timeout=20) as client:
            resp = await client.request(method, f"{API_URL}{path}", json=json, headers=headers)
    except httpx2.HTTPError as exc:
        raise ServiceError(f"No se pudo consultar GitHub: {exc}", 502) from exc
    if resp.status_code == 401:
        raise ServiceError("GITHUB_TOKEN inválido o vencido", 502)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("message")
        except ValueError:
            detail = resp.text[:200]
        raise ServiceError(f"GitHub respondió {resp.status_code}: {detail}", 404 if resp.status_code == 404 else 502)
    return resp.content if raw else resp.json()


async def _head(owner: str, name: str, branch: str, force: bool = False) -> str:
    """Commit actual de la rama base."""
    key = (owner, name, branch)
    if (not force and _head_cache["key"] == key
            and time.monotonic() - _head_cache["at"] < settings.GITHUB_CACHE_SECONDS):
        return _head_cache["sha"]
    ref = await _rest("GET", f"/repos/{owner}/{name}/git/ref/heads/{quote(branch)}")
    _head_cache.update(at=time.monotonic(), key=key, sha=ref["object"]["sha"])
    return _head_cache["sha"]


async def _tree(owner: str, name: str, sha: str) -> dict:
    key = (owner, name, sha)
    if _tree_cache["key"] != key:
        data = await _rest("GET", f"/repos/{owner}/{name}/git/trees/{sha}?recursive=1")
        # `commit` = submódulo: no se puede navegar, se omite.
        entries = [{"path": e["path"], "type": "dir" if e["type"] == "tree" else "file",
                    "size": e.get("size"), "mode": e["mode"]}
                   for e in data["tree"] if e["type"] in ("blob", "tree")]
        _tree_cache.update(key=key, data={"truncated": data.get("truncated", False), "entries": entries})
    return _tree_cache["data"]


def _files(tree_data: dict) -> dict[str, dict]:
    return {e["path"]: e for e in tree_data["entries"] if e["type"] == "file"}


def _check_path(path: str) -> str:
    path = (path or "").strip()
    parts = path.split("/")
    if not path or path.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ServiceError(f"Ruta inválida: '{path}'")
    if _FORBIDDEN_PATH.match(path):
        raise ServiceError(f"No se pueden modificar archivos de .github/ desde Optidev: '{path}'")
    return path


async def _read(owner: str, name: str, sha: str, files: dict[str, dict], path: str) -> dict:
    entry = files.get(path)
    if not entry:
        raise NotFoundError(f"No existe el archivo '{path}'")
    result = {"path": path, "size": entry["size"], "content": None, "binary": False, "tooLarge": False}
    if entry["size"] > MAX_FILE_BYTES:
        result["tooLarge"] = True
        return result
    raw = await _rest("GET", f"/repos/{owner}/{name}/contents/{quote(path)}?ref={sha}", raw=True)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None or "\x00" in text:
        result["binary"] = True
    else:
        result["content"] = text
    return result


def _repo_info(repo: Repository, owner: str, name: str) -> dict:
    return {"key": repo.key, "name": repo.name, "githubUrl": f"https://github.com/{owner}/{name}",
            "branch": repo.default_branch}


async def tree(db: AsyncSession, force: bool = False) -> dict:
    repo, owner, name = await _target(db)
    sha = await _head(owner, name, repo.default_branch, force)
    data = await _tree(owner, name, sha)
    return {"repository": _repo_info(repo, owner, name), "sha": sha, "truncated": data["truncated"],
            "entries": [{k: e[k] for k in ("path", "type", "size")} for e in data["entries"]]}


async def read_file(db: AsyncSession, path: str, ref: str | None = None) -> dict:
    """Contenido de un archivo en `ref` (un sha; por defecto, la rama base)."""
    repo, owner, name = await _target(db)
    sha = ref or await _head(owner, name, repo.default_branch)
    files = _files(await _tree(owner, name, sha))
    result = await _read(owner, name, sha, files, path)
    result["githubUrl"] = f"https://github.com/{owner}/{name}/blob/{sha}/{quote(path)}"
    return result


async def _claude(**params):
    """Una llamada a la API de Claude. En stream para que una respuesta larga
    (una propuesta con archivos completos) no corte por timeout."""
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        async with client.messages.stream(**params) as stream:
            return await stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise ServiceError("ANTHROPIC_API_KEY inválida", 502) from exc
    except anthropic.RateLimitError as exc:
        raise ServiceError("Se alcanzó el límite de uso de la API de Claude; prueba de nuevo en un rato", 429) from exc
    except anthropic.APIStatusError as exc:
        raise ServiceError(f"La API de Claude respondió {exc.status_code}: {exc.message}", 502) from exc
    except anthropic.APIConnectionError as exc:
        raise ServiceError(f"No se pudo conectar con la API de Claude: {exc}", 502) from exc


def _diff(path: str, old: str | None, new: str | None) -> str:
    lines = difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(),
                                 "/dev/null" if old is None else f"a/{path}",
                                 "/dev/null" if new is None else f"b/{path}", lineterm="")
    return "\n".join(lines)


async def _proposal(data: dict, read, files: dict[str, dict], sha: str) -> dict:
    """Valida la propuesta de Claude y le agrega el diff de cada archivo."""
    title = (data.get("title") or "").strip()
    changes = data.get("files") or []
    if not title or not changes:
        raise ServiceError("La propuesta necesita un título y al menos un archivo")
    result = []
    for change in changes:
        path = _check_path(change.get("path"))
        delete = bool(change.get("delete"))
        content = None if delete else change.get("content")
        if delete and path not in files:
            raise ServiceError(f"No se puede borrar '{path}': no existe")
        if not delete and not isinstance(content, str):
            raise ServiceError(f"Falta el contenido completo de '{path}'")
        old = (await read(path))["content"] if path in files else None
        if path in files and old is None:
            raise ServiceError(f"'{path}' es binario o muy grande: no se puede modificar desde acá")
        result.append({"path": path, "content": content, "isNew": path not in files, "diff": _diff(path, old, content)})
    return {"title": title[:200], "description": (data.get("description") or "").strip(), "baseSha": sha,
            "files": result}


async def chat(db: AsyncSession, messages: list[dict]) -> dict:
    """Responde el último mensaje del usuario. Devuelve el texto de Claude y,
    si armó una, la propuesta de cambios (sin aplicar)."""
    if not settings.ANTHROPIC_API_KEY:
        raise ServiceError("Falta configurar ANTHROPIC_API_KEY en el backend para usar el chat", 503)
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise ServiceError("El historial debe empezar y terminar con un mensaje del usuario")
    repo, owner, name = await _target(db)
    sha = await _head(owner, name, repo.default_branch)
    tree_data = await _tree(owner, name, sha)
    files = _files(tree_data)

    read_cache: dict[str, dict] = {}

    async def read(path: str) -> dict:
        if path not in read_cache:
            read_cache[path] = await _read(owner, name, sha, files, path)
        return read_cache[path]

    listing = "\n".join(sorted(files))
    system = (f"{SYSTEM_PROMPT}\n\nRepositorio: {owner}/{name}, rama base {repo.default_branch}.\n"
              f"Archivos ({len(files)}{', lista truncada por GitHub' if tree_data['truncated'] else ''}):\n{listing}")

    history: list = [{"role": m["role"], "content": m["content"]} for m in messages]
    proposal = None
    for _ in range(MAX_STEPS):
        response = await _claude(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=32000,
            system=system,
            tools=TOOLS,
            messages=history,
            output_config={"effort": "medium"},
            cache_control={"type": "ephemeral"},
        )
        if response.stop_reason == "max_tokens":
            raise ServiceError("La respuesta de Claude se cortó por largo; pide un cambio más acotado", 502)
        if response.stop_reason != "tool_use":
            break
        history.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                if block.name == "read_file":
                    file = await read(str(block.input.get("path", "")).strip())
                    if file["content"] is None:
                        raise ServiceError(f"'{file['path']}' es binario o pesa más de {MAX_FILE_BYTES // 1000} KB")
                    output = file["content"]
                elif block.name == "propose_changes":
                    proposal = await _proposal(block.input, read, files, sha)
                    output = ("Propuesta registrada. El usuario la ve con un botón para aprobarla y abrir el PR; "
                              "todavía no se aplicó nada.")
                else:
                    raise ServiceError(f"No existe la tool '{block.name}'")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            except ServiceError as exc:
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": exc.message,
                                "is_error": True})
        history.append({"role": "user", "content": results})
    else:
        raise ServiceError("Claude no terminó de responder (demasiados pasos); intenta con un pedido más acotado", 502)

    if response.stop_reason == "refusal":
        reply = "Claude no respondió este mensaje (lo rechazaron sus filtros de seguridad)."
    else:
        # El historial no admite mensajes vacíos: si solo propuso, se dice eso.
        reply = ("\n\n".join(b.text for b in response.content if b.type == "text").strip()
                 or ("Preparé una propuesta de cambios." if proposal else "(Sin respuesta)"))
    return {"reply": reply, "proposal": proposal, "sha": sha}


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40].strip("-") or "cambios"


async def create_pull_request(db: AsyncSession, actor: Actor, title: str, description: str, base_sha: str,
                              changes: list[dict]) -> dict:
    """Rama + commit (sobre `base_sha`) + PR contra la rama base del repo."""
    token = user_token(actor.user)
    repo, owner, name = await _target(db)
    for change in changes:
        change["path"] = _check_path(change["path"])
    base = f"/repos/{owner}/{name}"
    files = _files(await _tree(owner, name, base_sha))

    commit = await _rest("GET", f"{base}/git/commits/{base_sha}")
    new_tree = await _rest("POST", f"{base}/git/trees", json={
        "base_tree": commit["tree"]["sha"],
        "tree": [
            {"path": c["path"], "mode": files.get(c["path"], {}).get("mode", "100644"), "type": "blob",
             **({"sha": None} if c["content"] is None else {"content": c["content"]})}
            for c in changes
        ],
    }, token=token)
    new_commit = await _rest("POST", f"{base}/git/commits", json={
        "message": title, "tree": new_tree["sha"], "parents": [base_sha],
    }, token=token)
    branch = f"workflow/{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{_slug(title)}"
    await _rest("POST", f"{base}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": new_commit["sha"]},
                token=token)

    body = (f"{description}\n\n---\nPropuesto con Claude desde la pestaña Workflow de Optidev; "
            f"aprobado por {actor.user.name} (@{actor.user.username}).").strip()
    try:
        pr = await _rest("POST", f"{base}/pulls", json={
            "title": title, "head": branch, "base": repo.default_branch, "body": body,
        }, token=token)
    except ServiceError as exc:
        raise ServiceError(f"Se creó la rama {branch} pero no el PR: {exc.message}", exc.status_code) from exc
    return {"url": pr["html_url"], "number": pr["number"], "branch": branch}
