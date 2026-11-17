"""Salud de los repositorios del tablero: que sigan estandarizados.

Por cada repositorio cargado revisa, con la API de GitHub (GITHUB_TOKEN):

  1. **Bloqueo**: la rama `master` (REPO_HEALTH_BRANCHES) no acepta push directo:
     protección de rama clásica con PR obligatorio o con restricción de push, o un
     ruleset con la regla `pull_request`/`update`.
  2. **Staging**: existe la rama `staging`, de donde salen siempre los PR a master.
  3. **Labels**: exactamente los del repo de referencia (REPO_HEALTH_REFERENCE_REPO,
     la key de un repo del tablero; `mms-backend`), con el mismo color: ni uno más
     ni uno menos, más los del flujo de PRs (FLOW_LABELS: Aprobado, Revisado, Pendiente,
     Waiting y Breaking Change, con los colores de mms-backend) que el de referencia no
     tenga. Sin referencia configurada, solo los de FLOW_LABELS y no se penalizan los de más.
  4. **Privado**: el repo no es público.
  5. **GitHub Actions**: como mínimo los workflows de STANDARD_WORKFLOWS (por
     nombre; a dónde apunta cada uno puede cambiar), activos. Los workflows de
     más se informan (`extra`) pero no fallan.
  6. **CLAUDE.md** (informativo): existe en la raíz, con el enlace a `repo-docs/<repo>`
     (la documentación del repo de agentes) y una sección «Verificar».
  7. **Config de Claude** (informativo): `.claude/settings.json` declara el marketplace
     `optidev-tools` y habilita los plugins de CLAUDE_PLUGINS.
  8. **Documentación** (informativo): existe `repo-docs/<repo>/README.md` en el repo de
     agentes y el commit que dice documentar está a lo más a DOCS_MAX_COMMITS_BEHIND
     commits de la rama documentada.
  9. **CI** (informativo): algún workflow de `.github/workflows` corre tests y lint
     (CI_TESTS, CI_LINT).
 10. **Staging protegido** (informativo): `staging` tampoco acepta push directo.
 11. **CI del marketplace** (informativo, solo el repo de agentes): `master` exige status
     checks y existe el workflow MARKETPLACE_WORKFLOW.

El estándar es el mismo para todos; `describe_standard` lo expone para mostrarlo.

Cada chequeo da `ok`, `fail`, `unknown` (no se pudo verificar: sin permiso o
GitHub no respondió) o `na` (no aplica a ese repo); `unknown` no cuenta como falla.
Leer las protecciones de rama necesita un token con permisos de administración del repo.

Cada chequeo tiene una severidad en `CHECK_SPECS`: `required` (si falla, el repo no
cumple) o `info` (se muestra, pero no cuenta como falla ni lo arregla "Arreglar todo"),
para desplegar un chequeo nuevo sin poner todos los repos en rojo. Un chequeo también
puede limitarse a ciertos repos (`repos`: sus keys); en los demás queda `na`.

No se guarda nada en la BD: el último resultado queda en memoria y se sirve de ahí
(la configuración de un repo cambia poco). La primera vez que se pide se revisa;
después `monitor_loop` lo renueva cada REPO_HEALTH_INTERVAL_SECONDS (un día;
deja en el log lo que falla) y el botón "Revisar estándar" lo fuerza.
"""

import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import AsyncSessionLocal
from src.config.settings import settings
from src.models.repository import Repository
from src.services import approval_service, github_service, notification_service
from src.services.errors import ServiceError

logger = logging.getLogger(__name__)

REQUIRED = "required"
INFO = "info"
# Un chequeo nuevo parte como INFO y pasa a REQUIRED cuando termina su despliegue. `repos` (opcional):
# las keys de los repos a los que aplica; sin esa clave aplica a todos.
CHECK_SPECS = {
    "protection": {"severity": REQUIRED},
    "staging": {"severity": REQUIRED},
    "labels": {"severity": REQUIRED},
    "private": {"severity": REQUIRED},
    "actions": {"severity": REQUIRED},
    "claude-md": {"severity": INFO},
    "claude-config": {"severity": INFO},
    "docs": {"severity": INFO},
    "ci-quality": {"severity": INFO},
    "staging-protection": {"severity": INFO},
    "marketplace-ci": {"severity": INFO, "repos": [settings.WORKFLOW_REPO_KEY]},
}
CHECKS = tuple(CHECK_SPECS)
CHECK_TITLES = {
    "protection": "Bloqueo de ramas", "staging": "Rama staging", "labels": "Labels", "private": "Privado",
    "actions": "GitHub Actions", "claude-md": "CLAUDE.md", "claude-config": "Config de Claude", "docs": "Documentación",
    "ci-quality": "CI (tests y lint)", "staging-protection": "Staging protegido", "marketplace-ci": "CI del marketplace",
}
# El CI "corre tests y lint" si algún workflow ejecuta uno de estos comandos (heurística: no ejecuta nada).
CI_TESTS = re.compile(r"\b(pytest|jest|vitest|go test|phpunit|(yarn|npm|pnpm)( run)? test)\b")
CI_LINT = re.compile(r"\b(eslint|ruff|pylint|flake8|golangci-lint|(yarn|npm|pnpm)( run)? lint)\b")
# Workflow que valida el marketplace de agentes en cada PR; la rama protegida lo tiene que exigir como status check.
MARKETPLACE_WORKFLOW = "Validate marketplace"
# La documentación de cada repo vive en `repo-docs/<repo>/` del repo de agentes (WORKFLOW_REPO_KEY) y su README
# dice a qué commit corresponde; si el repo avanzó más de esto desde entonces, la documentación quedó atrás.
DOCS_DIR = "repo-docs"
DOCS_MAX_COMMITS_BEHIND = 50
# Estándar de agentes: el marketplace de plugins de Claude Code y los plugins que habilita cada repo.
CLAUDE_MARKETPLACE = "optidev-tools"
CLAUDE_PLUGINS = ["task-workflow-tools@optidev-tools"]
STAGING_BRANCH = "staging"
# Los workflows que como mínimo tiene que tener cada repo (los de mms-backend); puede tener más.
STANDARD_WORKFLOWS = ["Build and Push Docker Images", "Copilot code review", "Dependency graph", "Promote to Production"]
# Reglas de ruleset que impiden el push directo a la rama.
_BLOCKING_RULES = {"pull_request", "update"}
# Los labels del flujo de PRs (los usan optidev y las skills de revisión) con los colores de mms-backend. Forman
# siempre parte del estándar: son todo el estándar sin repo de referencia, y se suman a los del repo de referencia
# que no los tenga.
FLOW_LABELS = [
    {"name": approval_service.APPROVED_LABEL, "color": approval_service._LABEL_COLOR},
    {"name": approval_service.REVIEWED_LABEL, "color": approval_service._REVIEWED_COLOR},
    {"name": approval_service.PENDING_LABEL, "color": "7b1e66"},
    {"name": "Waiting", "color": "f9d0c4"},
    {"name": "Breaking Change", "color": "b60205"},
]
# Lo que exige y aplica el estándar de bloqueo (lo muestra la vista; ver repo_fix_service).
PROTECTION_RULES = [
    "Pull request obligatorio para entrar a la rama (sin exigir aprobaciones)",
    "También para admins",
    "Sin force push ni borrado de la rama",
]
MAX_LABEL_PAGES = 5
CONCURRENCY = 4

_report: dict = {"data": None}
_lock = asyncio.Lock()


def _result(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


def _unknown(exc: ServiceError) -> dict:
    return _result("unknown", exc.message)


def branches() -> list[str]:
    return [b.strip() for b in settings.REPO_HEALTH_BRANCHES.split(",") if b.strip()]


async def _repo_labels(owner: str, name: str) -> list[dict]:
    labels = []
    for page in range(1, MAX_LABEL_PAGES + 1):
        _, body = await github_service.rest("GET", f"/repos/{owner}/{name}/labels?per_page=100&page={page}")
        labels += [{"name": lbl["name"], "color": (lbl.get("color") or "").lower()} for lbl in body or []]
        if len(body or []) < 100:
            break
    return labels


async def _branch_blocks_push(owner: str, name: str, branch: str) -> dict:
    """Si el push directo a `branch` está bloqueado (protección clásica o ruleset)."""
    status, body = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{branch}/protection",
                                             allow=(403, 404))
    if status == 200 and (body.get("required_pull_request_reviews") or body.get("restrictions")):
        return _result("ok", f"{branch}: protegida (PR obligatorio o push restringido)", branch=branch)
    if status == 403 or (status == 404 and "not protected" not in str((body or {}).get("message", "")).lower()):
        return _result("unknown", f"{branch}: sin permiso para leer su protección (el token necesita admin del repo)",
                       branch=branch)
    # Sin protección clásica que bloquee: puede haber un ruleset.
    status, rules = await github_service.rest("GET", f"/repos/{owner}/{name}/rules/branches/{branch}", allow=(403, 404))
    if status == 403:
        return _result("unknown", f"{branch}: sin permiso para leer los rulesets", branch=branch)
    if status == 200 and any(r.get("type") in _BLOCKING_RULES for r in rules or []):
        return _result("ok", f"{branch}: bloqueada por un ruleset", branch=branch)
    return _result("fail", f"{branch} permite push directo (sin PR obligatorio ni restricción)", branch=branch)


async def check_protection(owner: str, name: str) -> dict:
    existing = []
    for branch in branches():
        status, _ = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{branch}", allow=(404,))
        if status == 200:
            existing.append(branch)
    if not existing:
        return _result("fail", f"No tiene rama {' ni '.join(branches())}")
    verdicts = [await _branch_blocks_push(owner, name, b) for b in existing]
    for state in ("fail", "unknown"):
        bad = [v for v in verdicts if v["status"] == state]
        if bad:
            return _result(state, "; ".join(v["message"] for v in bad))
    return _result("ok", "; ".join(v["message"] for v in verdicts))


async def check_labels(owner: str, name: str, standard: list[dict], exact: bool = True) -> dict:
    labels = {lbl["name"].lower(): lbl for lbl in await _repo_labels(owner, name)}
    expected = {lbl["name"].lower(): lbl for lbl in standard}
    missing = [e["name"] for key, e in expected.items() if key not in labels]
    wrong = [{"name": e["name"], "expected": e["color"], "actual": labels[key]["color"]}
             for key, e in expected.items() if key in labels and labels[key]["color"] != e["color"]]
    extra = sorted(lbl["name"] for key, lbl in labels.items() if key not in expected)
    parts = []
    if missing:
        parts.append(f"faltan: {', '.join(missing)}")
    if extra and exact:
        parts.append(f"de más: {', '.join(extra)}")
    if wrong:
        parts.append("color distinto: " + ", ".join(f"{w['name']} ({w['actual']} ≠ {w['expected']})" for w in wrong))
    extras = {"missing": missing, "wrongColor": wrong, "extra": extra}
    if parts:
        return _result("fail", "; ".join(parts), **extras)
    return _result("ok", f"{len(expected)} labels estándar con su color" + (", sin otros" if exact else ""), **extras)


async def check_staging(owner: str, name: str) -> dict:
    status, _ = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{STAGING_BRANCH}", allow=(404,))
    if status == 200:
        return _result("ok", f"Tiene la rama {STAGING_BRANCH}")
    return _result("fail", f"Falta la rama {STAGING_BRANCH} (los PR salen siempre de ahí)")


def _workflow_name(workflow: dict) -> str:
    return (workflow.get("name") or "").strip().lower()


async def _repo_workflows(owner: str, name: str) -> tuple[int, list[dict]]:
    status, body = await github_service.rest("GET", f"/repos/{owner}/{name}/actions/workflows?per_page=100",
                                             allow=(403, 404))
    if status != 200:
        return status, []
    return status, (body or {}).get("workflows") or []


async def check_actions(owner: str, name: str, expected: list[str]) -> dict:
    """`expected` = nombres de los workflows que como mínimo tiene que tener; los de más solo se informan."""
    status, workflows = await _repo_workflows(owner, name)
    if status != 200:
        return _result("unknown", "No se pudieron leer los workflows (¿Actions deshabilitado o sin permiso?)")
    have = {_workflow_name(w): w for w in workflows}
    wanted = {e.lower(): e for e in expected}
    missing = [e for key, e in wanted.items() if key not in have]
    disabled = [e for key, e in wanted.items() if key in have and have[key].get("state") != "active"]
    extra = sorted(w["name"] for key, w in have.items() if key not in wanted)
    parts = ([f"faltan: {', '.join(missing)}"] if missing else []) + (
        [f"deshabilitados: {', '.join(disabled)}"] if disabled else [])
    extras = {"missing": missing, "disabled": disabled, "extra": extra}
    if parts:
        return _result("fail", "; ".join(parts), **extras)
    return _result("ok", f"Tiene los {len(expected)} workflows estándar activos", **extras)


def _all_unknown(message: str) -> dict:
    """Todos los chequeos sin verificar (el repo no se pudo leer), con su severidad."""
    return {c: {**_result("unknown", message), "severity": spec["severity"]} for c, spec in CHECK_SPECS.items()}


def not_applicable(check: str, key: str) -> str | None:
    """Por qué `check` no aplica al repo `key` (None = aplica)."""
    repos = CHECK_SPECS[check].get("repos")
    return None if repos is None or key in repos else f"solo aplica a {', '.join(repos)}"


def _with_specs(key: str, checks: dict) -> dict:
    """Cada resultado con su severidad; los chequeos que no aplican al repo quedan en `na`."""
    out = {}
    for check in CHECKS:
        reason = not_applicable(check, key)
        result = _result("na", f"No aplica: {reason}") if reason else checks[check]
        out[check] = {**result, "severity": CHECK_SPECS[check]["severity"]}
    return out


async def _read_file(owner: str, name: str, path: str, ref: str | None = None) -> str | None:
    """Contenido de un archivo del repo (en `ref`, por defecto su rama por defecto), o None si no existe."""
    query = f"?ref={quote(ref)}" if ref else ""
    status, body = await github_service.rest("GET", f"/repos/{owner}/{name}/contents/{path}{query}", allow=(404,))
    if status != 200 or not isinstance(body, dict) or body.get("type") != "file":
        return None
    return base64.b64decode(body.get("content") or "").decode("utf-8", "replace")


def has_docs_link(text: str, name: str) -> bool:
    return bool(re.search(rf"repo-docs/{re.escape(name)}(?![\w.-])", text))


def has_verify_section(text: str) -> bool:
    return bool(re.search(r"^#+\s*verific", text, re.IGNORECASE | re.MULTILINE))


async def check_claude_md(owner: str, name: str) -> dict:
    """CLAUDE.md en la raíz, con el enlace a la documentación del repo y los comandos de verificación."""
    text = await _read_file(owner, name, "CLAUDE.md")
    if text is None:
        return _result("fail", "Falta CLAUDE.md en la raíz del repo")
    missing = []
    if not has_docs_link(text, name):
        missing.append(f"un enlace a repo-docs/{name}")
    if not has_verify_section(text):
        missing.append("una sección «Verificar» con los comandos de prueba y build")
    if missing:
        return _result("fail", f"CLAUDE.md no tiene {' ni '.join(missing)}")
    return _result("ok", "Tiene CLAUDE.md con enlace a su documentación y sección «Verificar»")


async def check_claude_config(owner: str, name: str) -> dict:
    """`.claude/settings.json` declara el marketplace de Optidev y habilita los plugins estándar."""
    text = await _read_file(owner, name, ".claude/settings.json")
    if text is None:
        return _result("fail", "Falta .claude/settings.json")
    try:
        config = json.loads(text)
    except ValueError:
        return _result("fail", ".claude/settings.json no es un JSON válido")
    config = config if isinstance(config, dict) else {}
    marketplaces = config.get("extraKnownMarketplaces")
    plugins = config.get("enabledPlugins")
    has_marketplace = isinstance(marketplaces, dict) and CLAUDE_MARKETPLACE in marketplaces
    enabled = plugins if isinstance(plugins, dict) else {}
    missing = [] if has_marketplace else [f"el marketplace {CLAUDE_MARKETPLACE}"]
    missing += [f"el plugin {p}" for p in CLAUDE_PLUGINS if enabled.get(p) is not True]
    if missing:
        return _result("fail", f".claude/settings.json no declara {' ni '.join(missing)}")
    return _result("ok", "Declara el marketplace y habilita los plugins estándar")


async def _workflow_texts(owner: str, name: str) -> list[str]:
    """Contenido de los archivos de `.github/workflows` (vacío si el repo no tiene)."""
    status, body = await github_service.rest("GET", f"/repos/{owner}/{name}/contents/.github/workflows", allow=(404,))
    if status != 200 or not isinstance(body, list):
        return []
    paths = [f["path"] for f in body if f.get("type") == "file" and f["name"].endswith((".yml", ".yaml"))]
    texts = []
    for path in paths:
        texts.append(await _read_file(owner, name, path) or "")
    return texts


async def check_ci_quality(owner: str, name: str) -> dict:
    """Algún workflow corre tests y lint."""
    text = "\n".join(await _workflow_texts(owner, name))
    if not text:
        return _result("fail", "No tiene workflows de CI en .github/workflows")
    missing = [label for label, command in (("tests", CI_TESTS), ("lint", CI_LINT)) if not command.search(text)]
    if missing:
        return _result("fail", f"El CI no corre {' ni '.join(missing)}")
    return _result("ok", "El CI corre tests y lint")


async def check_staging_protection(owner: str, name: str) -> dict:
    """`staging` tampoco acepta push directo (si no existe, ya lo dice el chequeo de la rama staging)."""
    status, _ = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{STAGING_BRANCH}", allow=(404,))
    if status != 200:
        return _result("unknown", f"No tiene rama {STAGING_BRANCH}")
    return await _branch_blocks_push(owner, name, STAGING_BRANCH)


async def _requires_status_checks(owner: str, name: str, branch: str) -> bool | None:
    """Si `branch` exige status checks (protección clásica o ruleset); None si no se pudo leer."""
    status, body = await github_service.rest("GET", f"/repos/{owner}/{name}/branches/{branch}/protection",
                                             allow=(403, 404))
    required = (body or {}).get("required_status_checks") if status == 200 else None
    if required and (required.get("checks") or required.get("contexts")):
        return True
    rules_status, rules = await github_service.rest("GET", f"/repos/{owner}/{name}/rules/branches/{branch}",
                                                    allow=(403, 404))
    if rules_status == 200 and any(r.get("type") == "required_status_checks" for r in rules or []):
        return True
    return None if 403 in (status, rules_status) else False


async def check_marketplace_ci(owner: str, name: str) -> dict:
    """El marketplace de agentes se valida en cada PR: su rama protegida exige el workflow de validación."""
    branch = branches()[0]
    required = await _requires_status_checks(owner, name, branch)
    workflows_status, workflows = await _repo_workflows(owner, name)
    if required is None or workflows_status != 200:
        return _result("unknown", "No se pudo leer la protección de la rama o los workflows (falta permiso de admin)")
    missing = [] if required else [f"{branch} no exige status checks"]
    if MARKETPLACE_WORKFLOW.lower() not in {_workflow_name(w) for w in workflows}:
        missing.append(f"falta el workflow {MARKETPLACE_WORKFLOW}")
    if missing:
        return _result("fail", "; ".join(missing))
    return _result("ok", f"{branch} exige status checks y existe el workflow {MARKETPLACE_WORKFLOW}")


def _front_matter(text: str) -> dict:
    """Pares `clave: valor` del front-matter (entre `---`) de un markdown; {} si no tiene."""
    match = re.match(r"---\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        return {}
    pairs = (line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return {key.strip(): value.strip() for key, value in pairs}


async def check_docs(owner: str, name: str, docs_repo: tuple[str, str] | None) -> dict:
    """Existe `repo-docs/<repo>/` en el repo de agentes y no está más de N commits atrás del código."""
    if docs_repo is None:
        return _result("unknown", f"El repo de documentación '{settings.WORKFLOW_REPO_KEY}' no está cargado en el tablero")
    path = f"{DOCS_DIR}/{name}/README.md"
    text = await _read_file(*docs_repo, path)
    if text is None:
        return _result("fail", f"No tiene documentación: falta {path} en el repo de agentes")
    front = _front_matter(text)
    sha, branch = front.get("documentado_en_commit"), front.get("documentado_en_rama")
    if not sha or not branch:
        return _result("fail", f"{path} no indica documentado_en_commit y documentado_en_rama")
    # El README guarda el SHA corto: se resuelve primero para comparar con el completo.
    status, commit = await github_service.rest("GET", f"/repos/{owner}/{name}/commits/{sha}", allow=(404, 422))
    if status != 200:
        return _result("fail", f"El commit documentado ({sha}) no existe en el repo")
    status, diff = await github_service.rest("GET", f"/repos/{owner}/{name}/compare/{commit['sha']}...{branch}",
                                             allow=(404,))
    if status != 200:
        return _result("fail", f"La rama documentada ({branch}) no existe en el repo")
    behind = diff.get("ahead_by", 0)
    if behind > DOCS_MAX_COMMITS_BEHIND:
        return _result("fail", f"La documentación está {behind} commits atrás de {branch} (máximo {DOCS_MAX_COMMITS_BEHIND})",
                       behind=behind)
    return _result("ok", f"Documentación a {behind} commits de {branch}", behind=behind)


async def check_repository(key: str, owner: str, name: str, standard: dict | None,
                           standard_error: str | None, docs_repo: tuple[str, str] | None) -> dict:
    """Los chequeos de un repo. Un fallo de GitHub en uno no afecta a los demás."""
    checks: dict = {}
    status, repo = await github_service.rest("GET", f"/repos/{owner}/{name}", allow=(403, 404))
    if status != 200:
        message = "No se pudo leer el repositorio (renombrado, sin permiso o inexistente)"
        return {"error": message, "checks": _all_unknown(message), "status": "unknown"}
    checks["private"] = (_result("ok", "Es privado") if repo.get("private")
                         else _result("fail", "Es público"))

    async def run(check: str, make_check):
        if not_applicable(check, key):
            return
        try:
            checks[check] = await make_check()
        except ServiceError as exc:
            checks[check] = _unknown(exc)

    await run("protection", lambda: check_protection(owner, name))
    await run("staging", lambda: check_staging(owner, name))
    await run("actions", lambda: check_actions(owner, name, STANDARD_WORKFLOWS))
    await run("claude-md", lambda: check_claude_md(owner, name))
    await run("claude-config", lambda: check_claude_config(owner, name))
    await run("docs", lambda: check_docs(owner, name, docs_repo))
    await run("ci-quality", lambda: check_ci_quality(owner, name))
    await run("staging-protection", lambda: check_staging_protection(owner, name))
    await run("marketplace-ci", lambda: check_marketplace_ci(owner, name))
    if standard is None:
        checks["labels"] = _result("unknown", standard_error or "No hay labels estándar")
    else:
        await run("labels", lambda: check_labels(owner, name, standard["labels"], exact=standard["exact"]))
    checks = _with_specs(key, checks)
    return {"error": None, "checks": checks, "status": _overall(checks)}


def _overall(checks: dict) -> str:
    """Estado del repo según los chequeos exigidos que le aplican (los informativos no cuentan)."""
    states = {c["status"] for c in checks.values() if c["severity"] == REQUIRED}
    return "fail" if "fail" in states else "unknown" if "unknown" in states else "ok"


async def _targets(db: AsyncSession) -> list[tuple[Repository, tuple[str, str] | None]]:
    repos = list((await db.execute(select(Repository).where(Repository.deleted_at.is_(None))
                                   .order_by(Repository.key))).scalars())
    return [(repo, github_service.parse_github_url(repo.github_url)) for repo in repos]


async def resolve_standard(targets: list) -> tuple[dict | None, str | None]:
    """Estándar → ({labels, exact}, None) o (None, motivo por el que no se pudo leer).
    `exact`: los labels de más también fallan (solo con repo de referencia)."""
    if not settings.REPO_HEALTH_REFERENCE_REPO:
        return {"labels": FLOW_LABELS, "exact": False}, None
    reference = next((gh for repo, gh in targets if repo.key == settings.REPO_HEALTH_REFERENCE_REPO and gh), None)
    if not reference:
        return None, f"El repo de referencia '{settings.REPO_HEALTH_REFERENCE_REPO}' no está cargado"
    try:
        labels = await _repo_labels(*reference)
    except ServiceError as exc:
        return None, f"No se pudo leer el repo de referencia: {exc.message}"
    named = {lbl["name"].lower() for lbl in labels}
    return {"labels": labels + [lbl for lbl in FLOW_LABELS if lbl["name"].lower() not in named], "exact": True}, None


def describe_standard(standard: dict | None, error: str | None) -> dict:
    """El estándar tal como lo ve la vista (mismo para todos los repos)."""
    return {
        "referenceRepo": settings.REPO_HEALTH_REFERENCE_REPO or None,
        "branches": branches(),
        "stagingBranch": STAGING_BRANCH,
        "protection": PROTECTION_RULES,
        "private": True,
        "labels": standard["labels"] if standard else None,
        "labelsExact": bool(standard and standard["exact"]),
        "workflows": STANDARD_WORKFLOWS,
        "claude": {"marketplace": CLAUDE_MARKETPLACE, "plugins": CLAUDE_PLUGINS},
        "docs": {"repo": settings.WORKFLOW_REPO_KEY, "maxCommitsBehind": DOCS_MAX_COMMITS_BEHIND},
        "marketplaceWorkflow": MARKETPLACE_WORKFLOW,
        "checks": [{"key": c, "severity": spec["severity"], "repos": spec.get("repos")}
                   for c, spec in CHECK_SPECS.items()],
        "error": error,
    }


def docs_source(targets: list) -> tuple[str, str] | None:
    """(owner, nombre) en GitHub del repo de agentes, donde vive `repo-docs/` (key WORKFLOW_REPO_KEY)."""
    return next((gh for repo, gh in targets if repo.key == settings.WORKFLOW_REPO_KEY and gh), None)


async def check_item(repo: Repository, gh, standard: dict | None, standard_error: str | None,
                     docs: tuple[str, str] | None) -> dict:
    """Resultado de un repo del tablero (con sus datos), sin lanzar excepciones."""
    item = {"key": repo.key, "name": repo.name, "color": repo.color, "githubUrl": repo.github_url}
    if not gh:
        message = "Sin URL de GitHub válida"
        return {**item, "error": message, "status": "unknown", "checks": _all_unknown(message)}
    try:
        return {**item, **await check_repository(repo.key, *gh, standard, standard_error, docs)}
    except ServiceError as exc:
        return {**item, "error": exc.message, "status": "unknown", "checks": _all_unknown(exc.message)}


def _summary(results: list[dict]) -> dict:
    return {s: sum(1 for r in results if r["status"] == s) for s in ("ok", "fail", "unknown")}


def _check_summary(results: list[dict]) -> dict:
    """Por chequeo, cuántos repos están en cada estado (`na` = no aplica): el avance de cada uno."""
    return {c: {s: sum(1 for r in results if r["checks"][c]["status"] == s) for s in ("ok", "fail", "unknown", "na")}
            for c in CHECKS}


def store_item(item: dict) -> None:
    """Reemplaza en el resultado en memoria el repo ya revisado (tras arreglarlo)."""
    data = _report["data"]
    if not data:
        return
    repos = [item if r["key"] == item["key"] else r for r in data["repositories"]]
    if all(r["key"] != item["key"] for r in repos):
        repos.append(item)  # un repo cargado después de la última revisión
    _report["data"] = {**data, "repositories": repos, "summary": _summary(repos),
                       "checkSummary": _check_summary(repos)}


def _alert(item: dict) -> dict:
    """Datos del aviso a los admins: qué chequeos exigidos fallan y cuáles no se pudieron verificar."""
    required = {check: result for check, result in item["checks"].items() if result["severity"] == REQUIRED}
    return {"key": item["key"], "name": item["name"],
            "failing": [c for c, result in required.items() if result["status"] == "fail"],
            "unknown": [c for c, result in required.items() if result["status"] == "unknown"]}


async def check_new_repository(db: AsyncSession, repo: Repository) -> None:
    """Revisa un repo recién cargado (o con otra URL de GitHub): deja el resultado en el reporte en memoria y, si
    no cumple el estándar o no se pudo verificar, avisa a los admins. No lanza nunca: la revisión es informativa y
    no puede impedir cargar el repo."""
    if not settings.GITHUB_TOKEN or not repo.github_url:
        return
    try:
        targets = await _targets(db)
        standard, standard_error = await resolve_standard(targets)
        gh = github_service.parse_github_url(repo.github_url)
        item = await check_item(repo, gh, standard, standard_error, docs_source(targets))
        store_item(item)
        if item["status"] != "ok":
            await notification_service.notify_admins(db, "repo_out_of_standard", _alert(item))
            await db.commit()
    except Exception:
        logger.exception("No se pudo revisar el repo %s al cargarlo", repo.key)


async def check_all(db: AsyncSession) -> dict:
    """Revisa todos los repos del tablero (los que tienen URL de GitHub válida) y deja el resultado en memoria."""
    if not settings.GITHUB_TOKEN:
        raise ServiceError("Falta configurar GITHUB_TOKEN en el backend para revisar los repositorios", 503)
    targets = await _targets(db)
    standard, standard_error = await resolve_standard(targets)
    docs = docs_source(targets)
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(repo: Repository, gh):
        async with semaphore:
            return await check_item(repo, gh, standard, standard_error, docs)

    results = await asyncio.gather(*(one(repo, gh) for repo, gh in targets))
    data = {
        "repositories": list(results),
        "summary": _summary(results),
        "checkSummary": _check_summary(results),
        "referenceRepo": settings.REPO_HEALTH_REFERENCE_REPO or None,
        "branches": branches(),
        "standard": describe_standard(standard, standard_error),
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }
    _report["data"] = data
    return data


async def report(db: AsyncSession, force: bool = False) -> dict:
    """Último resultado en memoria; si todavía no hay (o `force`), revisa ahora.
    Dos revisiones no corren a la vez: la segunda reutiliza el resultado de la primera."""
    if not force and _report["data"]:
        return _report["data"]
    started = datetime.now(timezone.utc)
    async with _lock:
        data = _report["data"]
        if data and datetime.fromisoformat(data["checkedAt"]) >= started:
            return data
        return await check_all(db)


def _log_failures(data: dict) -> None:
    for repo in data["repositories"]:
        failed = [f"{name}: {c['message']}" for name, c in repo["checks"].items()
                  if c["status"] == "fail" and c.get("severity") != INFO]
        if failed:
            logger.warning("Repo %s fuera del estándar: %s", repo["key"], " | ".join(failed))


async def monitor_loop() -> None:
    """Renueva la revisión cada REPO_HEALTH_INTERVAL_SECONDS mientras viva el backend. No revisa al
    arrancar (la primera vista lo hace y queda en caché). Un error de GitHub no corta el ciclo.
    Sin GITHUB_TOKEN no consulta nada."""
    while True:
        await asyncio.sleep(settings.REPO_HEALTH_INTERVAL_SECONDS)
        if settings.GITHUB_TOKEN:
            try:
                async with AsyncSessionLocal() as db:
                    _log_failures(await report(db, force=True))
            except asyncio.CancelledError:
                raise
            except ServiceError as exc:
                logger.warning("No se pudo revisar la salud de los repos: %s", exc.message)
            except Exception:
                logger.exception("Error revisando la salud de los repos")
