"""Label "Aprobado" en los PRs de una tarjeta que pasó QA desde Testing.

Es la misma convención que usa `scripts/rebase-approved-prs.sh` de Helios: un
PR con el label "Aprobado" está listo para rebasearse sobre staging y
mergearse. Los PRs se leen con GITHUB_TOKEN; los labels se ponen y sacan (y se
crean) con el token personal de quien aprueba o rechaza.

Un PR queda "Aprobado" solo si pasó los dos pasos: la revisión de código de
la skill revisar-pr (optidev-agents) sin pendientes y QA. Por eso no se
aprueba un PR con "Revisado" (comentarios sin resolver) o "Waiting" (depende de
otro PR); al aprobar se le saca "Pendiente".

Poner un label es idempotente: si algo falla a mitad de camino se puede volver
a aprobar sin efectos duplicados.

Rechazar en QA hace lo inverso: a los PRs abiertos les saca "Aprobado" y les
devuelve "Revisado" (necesitan cambios: no están listos para mergearse). Siguen
abiertos; "Mergear PR" no los toma hasta una nueva aprobación.
"""

from urllib.parse import quote

from src.services import github_service
from src.services.errors import ServiceError

APPROVED_LABEL = "Aprobado"
PENDING_LABEL = "Pendiente"
REVIEWED_LABEL = "Revisado"
BLOCKING_LABELS = (REVIEWED_LABEL, "Waiting")
# Los colores son los de mms-backend, el repo de referencia del estándar de labels (repo_health_service).
_LABEL_COLOR = "1d76db"
_LABEL_DESCRIPTION = "Pasó QA en optidev: listo para rebasear y mergear"
# Normalmente "Revisado" ya existe en el repo (lo crea revisar-pr); si no, se crea.
_REVIEWED_COLOR = "1caf55"
_REVIEWED_DESCRIPTION = "Necesita cambios: no está listo para mergearse"


def _has_label(labels: list, label: str) -> bool:
    """`labels` de la API REST (dicts con `name`) o nombres sueltos."""
    names = [(lbl.get("name") if isinstance(lbl, dict) else lbl) or "" for lbl in labels or []]
    return any(n.strip().lower() == label.lower() for n in names)


def has_approved_label(labels: list) -> bool:
    return _has_label(labels, APPROVED_LABEL)


def github_pull_urls(urls: list[str]) -> list[tuple[str, tuple[str, str, int]]]:
    """Los links de la tarjeta que son PRs de GitHub, con (owner, repo, número)."""
    out = []
    for url in urls or []:
        parsed = github_service.parse_pr_url(url)
        if parsed:
            out.append((url, parsed))
    return out


async def ensure_label(owner: str, name: str, token: str, label: str = APPROVED_LABEL, color: str = _LABEL_COLOR,
                       description: str = _LABEL_DESCRIPTION) -> None:
    """Crea el label en el repo si no existe ("Aprobado" por defecto)."""
    status, _ = await github_service.rest("GET", f"/repos/{owner}/{name}/labels/{quote(label)}",
                                          allow=(404,), token=token)
    if status == 404:
        # 422 = lo creó otro en el medio (ya existe): está bien igual.
        await github_service.rest("POST", f"/repos/{owner}/{name}/labels", allow=(422,), token=token, json={
            "name": label, "color": color, "description": description,
        })


async def label_pull_requests(urls: list[str], token: str) -> list[dict]:
    """Pone el label "Aprobado" en cada PR abierto de GitHub de la lista.

    Si algún PR abierto tiene "Revisado" o "Waiting" lanza `ServiceError` sin
    etiquetar ninguno. Si no, devuelve un resultado por PR: `status` =
    labeled | skipped | failed. No lanza por un fallo puntual de GitHub; quien
    llama decide qué hacer con los fallidos.
    """
    results = []
    to_label = []
    for url, (owner, name, number) in github_pull_urls(urls):
        item = {"url": url, "status": "labeled", "message": None}
        results.append(item)
        try:
            _, pr = await github_service.rest("GET", f"/repos/{owner}/{name}/pulls/{number}")
        except ServiceError as exc:
            item.update(status="failed", message=exc.message)
            continue
        if pr.get("merged_at"):
            item.update(status="skipped", message="ya está mergeado")
        elif pr.get("state") != "open":
            item.update(status="skipped", message="está cerrado")
        else:
            to_label.append((item, owner, name, number, pr.get("labels")))

    blocked = [f"{item['url']} ({label})" for item, *_, labels in to_label
               for label in BLOCKING_LABELS if _has_label(labels, label)]
    if blocked:
        raise ServiceError("No se puede aprobar: hay PRs que no pasaron la revisión de código "
                           "(revisar-pr) sin pendientes: " + ", ".join(blocked), 409)

    ensured: set[tuple[str, str]] = set()
    for item, owner, name, number, labels in to_label:
        try:
            if has_approved_label(labels):
                item["message"] = "ya tenía el label"
            else:
                if (owner, name) not in ensured:
                    await ensure_label(owner, name, token)
                    ensured.add((owner, name))
                await github_service.rest("POST", f"/repos/{owner}/{name}/issues/{number}/labels",
                                          json={"labels": [APPROVED_LABEL]}, token=token)
            if _has_label(labels, PENDING_LABEL):
                await github_service.rest(
                    "DELETE", f"/repos/{owner}/{name}/issues/{number}/labels/{quote(PENDING_LABEL)}",
                    allow=(404,), token=token)
        except ServiceError as exc:
            item.update(status="failed", message=exc.message)
    if results:
        github_service.invalidate_cache()
    return results


async def reject_pull_requests(urls: list[str], token: str) -> list[dict]:
    """Devuelve cada PR abierto de GitHub de la lista a "Revisado": le saca
    "Aprobado" (si lo tenía) y le pone "Revisado". No los cierra. Un resultado
    por PR: `status` = rejected | skipped | failed. No lanza por un fallo puntual
    de GitHub; quien llama decide qué hacer."""
    results = []
    ensured: set[tuple[str, str]] = set()
    for url, (owner, name, number) in github_pull_urls(urls):
        item = {"url": url, "status": "rejected", "message": None}
        results.append(item)
        try:
            _, pr = await github_service.rest("GET", f"/repos/{owner}/{name}/pulls/{number}")
            if pr.get("merged_at") or pr.get("state") != "open":
                item.update(status="skipped", message="ya está mergeado o cerrado")
                continue
            labels = pr.get("labels")
            if has_approved_label(labels):
                await github_service.rest(
                    "DELETE", f"/repos/{owner}/{name}/issues/{number}/labels/{quote(APPROVED_LABEL)}",
                    allow=(404,), token=token)
            if not _has_label(labels, REVIEWED_LABEL):
                if (owner, name) not in ensured:
                    await ensure_label(owner, name, token, REVIEWED_LABEL, _REVIEWED_COLOR, _REVIEWED_DESCRIPTION)
                    ensured.add((owner, name))
                await github_service.rest("POST", f"/repos/{owner}/{name}/issues/{number}/labels",
                                          json={"labels": [REVIEWED_LABEL]}, token=token)
        except ServiceError as exc:
            item.update(status="failed", message=exc.message)
    if results:
        github_service.invalidate_cache()
    return results
