"""Arreglos de la salud de un repo (ver repo_health_service) con un token temporal.

Corregir la configuración de un repo (proteger ramas, crear labels, hacerlo
privado, habilitar Actions) necesita permisos de administración que no se
quieren dejar en el backend: quien arregla pasa **un token de GitHub temporal**
en la petición. Ese token:

  - se usa solo durante esa petición (`github_service.use_token`, un
    ContextVar): ni se guarda ni se loguea ni vuelve en la respuesta;
  - lo pide un admin (la ruta es solo para admins) y se vuelve a usar para
    revisar el repo después de arreglarlo, así el resultado refleja lo que ese
    token puede leer.

Cada arreglo es idempotente y conservador: lo único que borra son los labels que
no son del estándar (el estándar es exacto), no pisa una protección de rama
existente (solo le agrega el PR obligatorio) y no inventa contenido (los workflows
que faltan no se copian, porque a dónde apuntan cambia por repo: avisa cuáles
agregar a mano).
Los arreglos que cambian archivos (`CLAUDE.md`, `.claude/settings.json`) no commitean
a la rama protegida: abren un PR contra `staging` desde la rama `estandar/<chequeo>`
(si ya hay una rama o un PR abierto, no abren otro). Con el mismo token, que
necesita permiso de escritura en Contents y Pull requests, además del de administración.
Resultado por chequeo: `fixed`, `ok` (ya estaba bien), `manual` (hay que hacerlo
a mano) o `failed` (GitHub lo rechazó; el mensaje dice por qué).
"""

import json
import logging
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.board_list import BoardList
from src.models.card import Card
from src.models.repository import Repository
from src.services import board_service, github_service, repo_health_service as health
from src.services.auth_service import Actor
from src.services.errors import NotFoundError, ServiceError

logger = logging.getLogger(__name__)

# Protección con la que se crea una rama sin ninguna: PR obligatorio (sin exigir
# aprobaciones, para no trabar a equipos chicos), también para admins, sin force push ni borrado.
_NEW_PROTECTION = {
    "required_status_checks": None,
    "enforce_admins": True,
    "required_pull_request_reviews": {"required_approving_review_count": 0},
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
}


def _sentence(parts: list[str]) -> str:
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def _out(status: str, message: str) -> dict:
    return {"status": status, "message": message}


async def fix_protection(owner: str, name: str) -> dict:
    messages = []
    for branch in health.branches():
        status, _ = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{branch}", allow=(404,))
        if status != 200:
            continue
        if (await health._branch_blocks_push(owner, name, branch))["status"] == "ok":
            messages.append(f"{branch}: ya estaba bloqueada")
            continue
        path = f"/repos/{owner}/{name}/branches/{branch}/protection"
        status, body = await github_service.rest("GET", path, allow=(403, 404))
        if status == 200:
            # Ya tiene protección (p. ej. checks obligatorios): solo se le agrega el PR obligatorio.
            await github_service.rest("PATCH", f"{path}/required_pull_request_reviews",
                                      json={"required_approving_review_count": 0})
            messages.append(f"{branch}: se agregó el PR obligatorio a su protección existente")
        elif status == 404 and "not protected" in str((body or {}).get("message", "")).lower():
            await github_service.rest("PUT", path, json=_NEW_PROTECTION)
            messages.append(f"{branch}: protegida (PR obligatorio, también para admins; sin force push ni borrado)")
        else:
            raise ServiceError(f"{branch}: el token no puede leer ni cambiar su protección (¿le falta admin del repo?)")
    if not messages:
        return _out("manual", f"No hay ninguna rama {' ni '.join(health.branches())} que proteger")
    fixed = any("ya estaba" not in m for m in messages)
    return _out("fixed" if fixed else "ok", "; ".join(messages))


async def fix_staging(owner: str, name: str) -> dict:
    """Crea `staging` desde la punta de la primera rama protegida (master) que exista."""
    if (await health.check_staging(owner, name))["status"] == "ok":
        return _out("ok", f"Ya tenía la rama {health.STAGING_BRANCH}")
    for branch in health.branches():
        status, body = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{branch}", allow=(404,))
        if status == 200:
            await github_service.rest("POST", f"/repos/{owner}/{name}/git/refs",
                                      json={"ref": f"refs/heads/{health.STAGING_BRANCH}", "sha": body["commit"]["sha"]})
            return _out("fixed", f"Se creó {health.STAGING_BRANCH} desde {branch}")
    return _out("manual", f"No hay ninguna rama {' ni '.join(health.branches())} desde la cual crear {health.STAGING_BRANCH}")


async def fix_labels(owner: str, name: str, standard: dict | None, standard_error: str | None) -> dict:
    if standard is None:
        raise ServiceError(standard_error or "No hay labels estándar")
    current = {lbl["name"].lower(): lbl for lbl in await health._repo_labels(owner, name)}
    created, recolored, deleted = [], [], []
    for label in standard["labels"]:
        existing = current.get(label["name"].lower())
        if not existing:
            await github_service.rest("POST", f"/repos/{owner}/{name}/labels",
                                      json={"name": label["name"], "color": label["color"]})
            created.append(label["name"])
        elif existing["color"] != label["color"]:
            await github_service.rest("PATCH", f"/repos/{owner}/{name}/labels/{quote(existing['name'])}",
                                      json={"color": label["color"]})
            recolored.append(label["name"])
    if standard["exact"]:
        wanted = {label["name"].lower() for label in standard["labels"]}
        for key, existing in current.items():
            if key not in wanted:
                await github_service.rest("DELETE", f"/repos/{owner}/{name}/labels/{quote(existing['name'])}")
                deleted.append(existing["name"])
    if not created and not recolored and not deleted:
        return _out("ok", "Los labels ya estaban bien")
    parts = ([f"creados: {', '.join(created)}"] if created else []) + (
        [f"color corregido: {', '.join(recolored)}"] if recolored else []) + (
        [f"borrados: {', '.join(deleted)}"] if deleted else [])
    return _out("fixed", "; ".join(parts))


async def fix_private(owner: str, name: str) -> dict:
    _, repo = await github_service.rest("GET", f"/repos/{owner}/{name}")
    if repo.get("private"):
        return _out("ok", "Ya era privado")
    await github_service.rest("PATCH", f"/repos/{owner}/{name}", json={"private": True})
    return _out("fixed", "Ahora es privado")


async def fix_actions(owner: str, name: str, expected: list[str]) -> dict:
    status, perms = await github_service.rest("GET", f"/repos/{owner}/{name}/actions/permissions", allow=(403, 404))
    enabled_now = False
    if status == 200 and perms.get("enabled") is False:
        await github_service.rest("PUT", f"/repos/{owner}/{name}/actions/permissions", json={"enabled": True})
        enabled_now = True
    _, workflows = await health._repo_workflows(owner, name)
    wanted = {e.lower(): e for e in expected}
    have = {health._workflow_name(w): w for w in workflows}
    missing = [e for key, e in wanted.items() if key not in have]
    disabled = [w for key, w in have.items() if key in wanted and w.get("state") != "active"]
    for workflow in disabled:
        await github_service.rest("PUT", f"/repos/{owner}/{name}/actions/workflows/{workflow['id']}/enable")
    done = (["se habilitó Actions en el repo"] if enabled_now else []) + (
        [f"se habilitaron {len(disabled)} workflow(s)"] if disabled else [])
    # No se copian: a dónde apuntan cambia por repo.
    if missing:
        done.append(f"faltan {', '.join(missing)}: hay que agregarlos a mano (adaptando a dónde apunta cada repo)")
    if missing:
        return _out("manual", _sentence(done))
    return _out("fixed", _sentence(done)) if done else _out("ok", "Actions ya estaba bien")


# Un PR por chequeo de archivos: la rama se llama igual siempre, así un segundo "Arreglar" no abre otro PR.
FILE_FIX_BRANCH = "estandar/{check}"


async def _open_pull_request(owner: str, name: str, check: str, title: str, description: str,
                             files: dict[str, str]) -> dict:
    """Rama `estandar/<check>` desde `staging` + un commit con `files` + PR contra `staging`.
    Devuelve el PR (url y número) o el que ya estaba abierto para esa rama."""
    base = f"/repos/{owner}/{name}"
    branch = FILE_FIX_BRANCH.format(check=check)
    status, tip = await github_service.rest("GET", f"{base}/branches/{health.STAGING_BRANCH}", allow=(404,))
    if status != 200:
        raise ServiceError(f"El repo no tiene la rama {health.STAGING_BRANCH}: arreglá antes ese chequeo")
    status, _ = await github_service.rest("GET", f"{base}/git/ref/heads/{quote(branch)}", allow=(404,))
    if status == 200:
        _, pulls = await github_service.rest("GET", f"{base}/pulls?state=open&head={owner}:{quote(branch)}")
        if pulls:
            return {"url": pulls[0]["html_url"], "number": pulls[0]["number"], "existing": True}
        raise ServiceError(f"Ya existe la rama {branch} sin PR abierto: borrala o abrí el PR a mano")
    parent = tip["commit"]["sha"]
    _, commit = await github_service.rest("GET", f"{base}/git/commits/{parent}")
    _, tree = await github_service.rest("POST", f"{base}/git/trees", json={
        "base_tree": commit["tree"]["sha"],
        "tree": [{"path": path, "mode": "100644", "type": "blob", "content": content}
                 for path, content in files.items()],
    })
    _, new_commit = await github_service.rest("POST", f"{base}/git/commits", json={
        "message": title, "tree": tree["sha"], "parents": [parent]})
    await github_service.rest("POST", f"{base}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": new_commit["sha"]})
    _, pull = await github_service.rest("POST", f"{base}/pulls", json={
        "title": title, "head": branch, "base": health.STAGING_BRANCH, "body": description})
    return {"url": pull["html_url"], "number": pull["number"], "existing": False}


def _pr_result(pull: dict, what: str) -> dict:
    if pull["existing"]:
        return _out("ok", f"Ya hay un PR abierto con {what}: {pull['url']}")
    return _out("fixed", f"Se abrió el PR {pull['url']} con {what} contra {health.STAGING_BRANCH}; "
                         "el chequeo se pone en verde cuando llegue a la rama principal")


async def _verify_commands(owner: str, name: str) -> list[str]:
    """Comandos de prueba y build que se pueden deducir de la raíz del repo; vacío si no hay certeza
    (p. ej. un monorepo con el código en subcarpetas): no se inventan."""
    ref = health.STAGING_BRANCH
    commands = []
    package = await health._read_file(owner, name, "package.json", ref)
    if package:
        try:
            scripts = json.loads(package).get("scripts") or {}
        except (ValueError, AttributeError):
            scripts = {}
        yarn = await health._read_file(owner, name, "yarn.lock", ref) is not None
        commands += [f"{'yarn' if yarn else 'npm run'} {script}" for script in ("test", "lint", "build") if script in scripts]
    pytest = await health._read_file(owner, name, "pytest.ini", ref)
    if pytest is not None or "pytest" in (await health._read_file(owner, name, "pyproject.toml", ref) or ""):
        commands.append("pytest -q")
    return commands


def claude_md_additions(existing: str | None, name: str, docs: tuple[str, str] | None,
                        commands: list[str]) -> tuple[str, list[str]]:
    """CLAUDE.md completo con lo que le falta (el enlace a repo-docs y la sección «Verificar»), conservando
    lo que ya tiene, y lo que no se pudo agregar."""
    text = existing if existing is not None else f"# {name}\n"
    text = text if text.endswith("\n") else text + "\n"
    pending = []
    if not health.has_docs_link(text, name):
        where = (f"https://github.com/{docs[0]}/{docs[1]}/tree/HEAD/{health.DOCS_DIR}/{name}" if docs
                 else f"{health.DOCS_DIR}/{name} en el repo de agentes")
        text += f"\nDocumentación del repo: [{health.DOCS_DIR}/{name}]({where})\n"
    if not health.has_verify_section(text):
        if commands:
            text += "\n## Verificar\n\n```bash\n" + "\n".join(commands) + "\n```\n"
        else:
            pending.append("la sección «Verificar» (no se pudo deducir de la raíz del repo qué comandos corren los tests y el build)")
    return text, pending


def merge_claude_settings(existing: str | None, marketplace_repo: str) -> str:
    """`.claude/settings.json` con el marketplace y los plugins estándar, conservando el resto de las claves.
    Un JSON que no se puede leer no se pisa."""
    try:
        config = json.loads(existing) if existing is not None else {}
    except ValueError as exc:
        raise ServiceError(".claude/settings.json no es un JSON válido: corregilo a mano para no perder su contenido") from exc
    if not isinstance(config, dict):
        raise ServiceError(".claude/settings.json no es un objeto JSON: corregilo a mano para no perder su contenido")
    marketplaces = config.get("extraKnownMarketplaces")
    marketplaces = dict(marketplaces) if isinstance(marketplaces, dict) else {}
    marketplaces.setdefault(health.CLAUDE_MARKETPLACE, {"source": {"source": "github", "repo": marketplace_repo}})
    plugins = config.get("enabledPlugins")
    plugins = dict(plugins) if isinstance(plugins, dict) else {}
    plugins.update({plugin: True for plugin in health.CLAUDE_PLUGINS})
    config = {**config, "extraKnownMarketplaces": marketplaces, "enabledPlugins": plugins}
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


async def fix_claude_md(owner: str, name: str, docs: tuple[str, str] | None) -> dict:
    if (await health.check_claude_md(owner, name))["status"] == "ok":
        return _out("ok", "CLAUDE.md ya estaba bien")
    existing = await health._read_file(owner, name, "CLAUDE.md", health.STAGING_BRANCH)
    text, pending = claude_md_additions(existing, name, docs, await _verify_commands(owner, name))
    if text == existing:
        return _out("manual", f"Falta {'; '.join(pending)}")
    pull = await _open_pull_request(
        owner, name, "claude-md", "chore: CLAUDE.md según el estándar de Optidev",
        "Agrega a CLAUDE.md lo que pide el estándar de Optidev (ver Configuración → Repositorios en optidev). "
        "Revisar que los comandos de «Verificar» sean los del repo.", {"CLAUDE.md": text})
    result = _pr_result(pull, "CLAUDE.md")
    if pending and not pull["existing"]:
        return _out("manual", f"{result['message']}. Falta a mano {'; '.join(pending)}")
    return result


async def fix_claude_config(owner: str, name: str, docs: tuple[str, str] | None) -> dict:
    if (await health.check_claude_config(owner, name))["status"] == "ok":
        return _out("ok", ".claude/settings.json ya estaba bien")
    if not docs:
        return _out("manual", f"No se sabe de dónde es el marketplace {health.CLAUDE_MARKETPLACE}: "
                              f"falta el repo con key {health.settings.WORKFLOW_REPO_KEY} en el tablero")
    existing = await health._read_file(owner, name, ".claude/settings.json", health.STAGING_BRANCH)
    pull = await _open_pull_request(
        owner, name, "claude-config", "chore: habilitar el marketplace y los plugins de Optidev",
        "Declara el marketplace y habilita los plugins estándar en .claude/settings.json, conservando lo demás.",
        {".claude/settings.json": merge_claude_settings(existing, "/".join(docs))})
    return _pr_result(pull, ".claude/settings.json")


CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
_CI_HEADER = "name: CI\n\non:\n  pull_request:\n    branches: [staging, master]\n\njobs:\n"
_JOB_HEAD = "  {job}:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"


def _node_job(scripts: dict, lockfiles: set[str], missing: list[str]) -> tuple[str | None, set[str]]:
    """Job de Node con los scripts `test`/`lint` que faltan en el CI y qué partes cubre (pnpm no se arma)."""
    if "pnpm-lock.yaml" in lockfiles:
        return None, set()
    yarn = "yarn.lock" in lockfiles
    run = "yarn" if yarn else "npm run"
    install = "yarn install --frozen-lockfile" if yarn else "npm ci" if "package-lock.json" in lockfiles else "npm install"
    covered = {part for part, script in (("tests", "test"), ("lint", "lint")) if part in missing and script in scripts}
    if not covered:
        return None, set()
    commands = [f"{run} {script}" for part, script in (("tests", "test"), ("lint", "lint")) if part in covered]
    steps = ("      - uses: actions/setup-node@v4\n        with:\n          node-version: \"22\"\n"
             + ("      - run: corepack enable || true\n" if yarn else "")
             + "".join(f"      - run: {command}\n" for command in [install, *commands]))
    return _JOB_HEAD.format(job="node") + steps, covered


def _python_job(files: dict[str, str], missing: list[str]) -> tuple[str | None, set[str]]:
    """Job de Python con pytest/ruff si el repo los declara y hay un requirements*.txt para instalar."""
    requirements = next((f for f in ("requirements-dev.txt", "requirements.txt") if f in files), None)
    declared = "\n".join(files.values())
    wanted = {"tests": ("pytest -q", "pytest.ini" in files or "pytest" in declared),
              "lint": ("ruff check .", "ruff.toml" in files or ".ruff.toml" in files or "ruff" in declared)}
    covered = {part for part, (_, found) in wanted.items() if part in missing and found}
    if not covered or not requirements:
        return None, set()
    commands = [f"pip install -r {requirements}"] + [wanted[part][0] for part in ("tests", "lint") if part in covered]
    steps = ("      - uses: actions/setup-python@v5\n        with:\n          python-version: \"3.11\"\n"
             + "".join(f"      - run: {command}\n" for command in commands))
    return _JOB_HEAD.format(job="python") + steps, covered


async def ci_workflow(owner: str, name: str, missing: list[str]) -> tuple[str | None, list[str]]:
    """Workflow de CI que corre lo que falta (`tests`/`lint`), deducido de la raíz del repo, y las partes que no
    se pudieron deducir (p. ej. un monorepo con el código en subcarpetas): no se inventan."""
    ref = health.STAGING_BRANCH
    package = await health._read_file(owner, name, "package.json", ref)
    try:
        scripts = (json.loads(package).get("scripts") or {}) if package else {}
    except (ValueError, AttributeError):
        scripts = {}
    lockfiles = {f for f in ("yarn.lock", "package-lock.json", "pnpm-lock.yaml")
                 if await health._read_file(owner, name, f, ref) is not None}
    python_files = {}
    for f in ("requirements-dev.txt", "requirements.txt", "pytest.ini", "ruff.toml", ".ruff.toml", "pyproject.toml"):
        text = await health._read_file(owner, name, f, ref)
        if text is not None:
            python_files[f] = text
    built = [_node_job(scripts, lockfiles, missing), _python_job(python_files, missing)]
    jobs = [job for job, _ in built if job]
    left = [part for part in missing if not any(part in covered for _, covered in built)]
    pending = [f"correr {' y '.join(left)} en el CI (no se pudo deducir de la raíz del repo cómo)"] if left else []
    return (_CI_HEADER + "\n".join(jobs) if jobs else None), pending


async def fix_ci_quality(owner: str, name: str) -> dict:
    """Abre un PR con un `ci.yml` que corre los tests y el lint que faltan. No toca workflows existentes
    (si ya hay un ci.yml, queda a mano) ni inventa comandos que la raíz del repo no muestre."""
    current = await health.check_ci_quality(owner, name)
    if current["status"] == "ok":
        return _out("ok", "El CI ya corre tests y lint")
    text = "\n".join(await health._workflow_texts(owner, name))
    missing = [part for part, command in (("tests", health.CI_TESTS), ("lint", health.CI_LINT)) if not command.search(text)]
    for path in (CI_WORKFLOW_PATH, CI_WORKFLOW_PATH.replace(".yml", ".yaml")):
        if await health._read_file(owner, name, path, health.STAGING_BRANCH) is not None:
            return _out("manual", f"Ya hay un {path} que no corre {' ni '.join(missing)}: agregarlo a mano ahí")
    workflow, pending = await ci_workflow(owner, name, missing)
    if workflow is None:
        return _out("manual", f"Falta {'; '.join(pending)}")
    pull = await _open_pull_request(
        owner, name, "ci-quality", "ci: correr tests y lint según el estándar de Optidev",
        f"Agrega {CI_WORKFLOW_PATH} con los pasos que faltaban ({' y '.join(missing)}), deducidos de la raíz del repo. "
        "Revisar versiones, variables de entorno y que los comandos sean los del repo.", {CI_WORKFLOW_PATH: workflow})
    result = _pr_result(pull, CI_WORKFLOW_PATH)
    if pending and not pull["existing"]:
        return _out("manual", f"{result['message']}. Falta a mano {'; '.join(pending)}")
    return result


async def fix_manual(message: str) -> dict:
    """Chequeos que todavía no se arreglan solos: se informa qué hay que hacer a mano."""
    return _out("manual", message)


async def fix_repository(db: AsyncSession, actor: Actor, key: str, token: str, checks: list[str] | None) -> dict:
    """Aplica los arreglos pedidos (por defecto, todos los chequeos que no estén OK) con `token`
    y revisa el repo de nuevo. Devuelve {results: {chequeo: {status, message}}, repository: <salud del repo>}."""
    token = (token or "").strip()
    if len(token) < 20:
        raise ServiceError("Pasá un token de GitHub con permisos de administración sobre el repo")
    if checks is not None and not set(checks) <= set(health.CHECKS):
        raise ServiceError(f"Chequeos desconocidos: {', '.join(sorted(set(checks) - set(health.CHECKS)))}")
    inapplicable = [c for c in checks or [] if health.not_applicable(c, key)]
    if inapplicable:
        raise ServiceError(f"No aplican a este repositorio: {', '.join(inapplicable)}")
    targets = await health._targets(db)
    found = next(((repo, gh) for repo, gh in targets if repo.key == key), None)
    if not found:
        raise NotFoundError("Repositorio no encontrado")
    repo, gh = found
    if not gh:
        raise ServiceError("El repositorio no tiene una URL de GitHub válida")
    owner, name = gh

    with github_service.use_token(token):
        standard, standard_error = await health.resolve_standard(targets)
        docs = health.docs_source(targets)
        if checks is None:
            current = await health.check_item(repo, gh, standard, standard_error, docs)
            # Solo lo exigido que no cumple: los informativos y los que no aplican al repo no se tocan.
            checks = [c for c in health.CHECKS if current["checks"][c]["status"] not in ("ok", "na")
                      and current["checks"][c]["severity"] == health.REQUIRED]
        fixers = {
            "protection": lambda: fix_protection(owner, name),
            "staging": lambda: fix_staging(owner, name),
            "labels": lambda: fix_labels(owner, name, standard, standard_error),
            "private": lambda: fix_private(owner, name),
            "actions": lambda: fix_actions(owner, name, health.STANDARD_WORKFLOWS),
            "claude-md": lambda: fix_claude_md(owner, name, docs),
            "claude-config": lambda: fix_claude_config(owner, name, docs),
            "docs": lambda: fix_manual(
                f"Actualizar {health.DOCS_DIR}/{name} en el repo de agentes y subir documentado_en_commit"),
            "ci-quality": lambda: fix_ci_quality(owner, name),
            # Se deja a mano: la skill pr-review-merge hace push directo a staging y la protección lo rompería.
            "staging-protection": lambda: fix_manual(
                f"Proteger {health.STAGING_BRANCH} con PR obligatorio. Antes, revisar que nada haga push directo a "
                f"{health.STAGING_BRANCH} (la skill pr-review-merge lo hace)"),
            "marketplace-ci": lambda: fix_manual(
                f"Crear el workflow {health.MARKETPLACE_WORKFLOW} y exigirlo como status check en {health.branches()[0]}"),
        }
        results = {}
        for check in health.CHECKS:
            if check not in checks:
                continue
            try:
                results[check] = await fixers[check]()
            except ServiceError as exc:
                results[check] = _out("failed", exc.message)
        item = await health.check_item(repo, gh, standard, standard_error, docs)
    health.store_item(item)
    logger.info("%s arregló %s: %s", actor.user.username, key, {c: r["status"] for c, r in results.items()})
    return {"results": results, "repository": item}


PENDING_CARD_TITLE = "Dejar {name} en el estándar"


async def _backlog(db: AsyncSession) -> BoardList:
    """La lista "Backlog"; si el tablero no tiene una con ese nombre, la primera ocultable o, si no, la primera."""
    lists = (await db.execute(select(BoardList).where(BoardList.deleted_at.is_(None))
                              .order_by(BoardList.position, BoardList.id))).scalars().all()
    if not lists:
        raise ServiceError("El tablero no tiene listas")
    return next((lst for lst in lists if lst.name.lower() == "backlog"),
                next((lst for lst in lists if lst.collapsible), lists[0]))


async def create_pending_card(db: AsyncSession, actor: Actor, key: str) -> dict:
    """Tarjeta en Backlog del repo con los chequeos que no cumple (según el último reporte), para lo que
    no se arregla solo. Si ya hay una sin archivar con ese título no crea otra: devuelve esa."""
    repo = (await db.execute(
        select(Repository).where(Repository.key == key, Repository.deleted_at.is_(None)))).scalar_one_or_none()
    if not repo:
        raise NotFoundError("Repositorio no encontrado")
    item = next((i for i in (await health.report(db))["repositories"] if i["key"] == key), None)
    pending = [(c, health.CHECK_TITLES[c], r) for c, r in (item or {}).get("checks", {}).items()
               if r["status"] in ("fail", "unknown")]
    if not pending:
        raise ServiceError("El repositorio cumple el estándar: no hay nada pendiente")
    title = PENDING_CARD_TITLE.format(name=repo.name)
    backlog = await _backlog(db)
    existing = (await db.execute(
        select(Card.id).where(Card.title == title, Card.archived_at.is_(None), Card.list_id == backlog.id,
                              Card.repositories.any(Repository.id == repo.id)))).scalars().first()
    if existing:
        return {"card": await board_service.get_card_detail(db, existing), "created": False}
    lines = [f"- **{label}** ({'exigido' if r['severity'] == health.REQUIRED else 'informativo'}): {r['message']}"
             for _, label, r in pending]
    description = ("Chequeos pendientes según Configuración → Repositorios:\n\n" + "\n".join(lines)
                   + "\n\nLos que se pueden arreglar con el botón *Arreglar* se hacen desde ahí; "
                     "el resto (documentación, workflows) a mano.")
    card = await board_service.create_card(db, actor, title=title, list_ref=backlog.id, description=description,
                                           repositories=[key])
    return {"card": card, "created": True}
