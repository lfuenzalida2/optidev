"""Skills de Claude sobre un PR, corridas con `claude -p` en la VM de pruebas del usuario.

Para PRs que quedaron "Revisado" (comentarios de revisión sin resolver) se
puede lanzar desde Testing una skill de Claude — por ahora `revisar-pr`, de
optidev-agents — en la VM del usuario, donde ya están instalados Claude Code
y su sesión (ver las herramientas de Claude Remote Control y la rutina diaria).

Corre de fondo como un `VmTask` (mismo log en vivo y mismo límite de una tarea
por VM que los arreglos del doctor) y queda un comentario en la tarjeta.

Conversación: la primera corrida abre una sesión de Claude con id propio
(`--session-id`, guardado en la tarea) y cada respuesta del usuario (`reply`)
la retoma con `--resume` como un turno más, en la misma tarea y el mismo log,
hasta que el usuario dé la revisión por terminada (por ejemplo para que Claude
suba los cambios al PR o corrija algo).

Seguridad: **el cliente solo elige una skill del catálogo y un link de PR que
ya esté registrado en una tarjeta**; el prompt inicial lo arma el servidor con
el link reconstruido a partir de (org, repo, número). Las respuestas son texto
libre del dueño de la VM (como lo que escribiría en su terminal), siempre
entre comillas de shell y con un largo máximo. Corre sin pedir permisos (`--dangerously-skip-permissions`,
como la rutina diaria: la VM de pruebas es desechable).
"""

import asyncio
import logging
import shlex
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import AsyncSessionLocal
from src.models.test_vm import TestVm
from src.models.vm_task import VmTask
from src.services import board_service, github_service, vm_doctor_service, vm_service
from src.services.auth_service import Actor
from src.services.errors import ConflictError, NotFoundError, ServiceError
from src.services.remote_job import JobLog, run_remote

logger = logging.getLogger(__name__)

# id → título. Agregar una skill = una línea acá (y su prompt en _PROMPTS; que exista en la VM).
SKILLS = {"revisar-pr": "Revisar PR", "release": "Preparar release"}
TASK_PREFIX = "skill:"
SKILL_TIMEOUT = 30 * 60
REPLY_MAX_LENGTH = 4000
# Skills que solo valen para un PR de una rama a otra: id → (head, base).
_PR_BRANCHES = {"release": ("staging", "master")}
_PROMPTS = {
    "revisar-pr": ("Usá la skill {skill} (con la tool Skill) sobre este pull request: {url}\n"
                   "Seguí sus instrucciones al pie de la letra. No mergees ni cierres el PR."),
    # El orden importa: el escáner y el solucionador van antes del commit "Version release"
    # (ver service-requirements-scanner, «Workflow order»).
    "release": ("Este pull request promueve staging a master: {url}\n"
                "Prepará el release en una copia limpia del repo (clonala en un directorio temporal con gh, en la "
                "rama staging al día con origin; si algo no está limpio o staging diverge, pará y avisá). "
                "Hacé estos pasos en orden:\n"
                "1. Subí la versión de cada servicio que cambió desde el último commit \"Version release\" "
                "(minor si hay feat, si no patch; si hay un breaking change pará y preguntá, nunca subas major "
                "por tu cuenta). En optidev-mms-backend, si cambió shared/, también hay que subir la versión "
                "de todos los servicios que lo usan. Usá el mecanismo propio del repo "
                "(por ejemplo sync-versions.mjs), sin editar a mano lo que ese script genera.\n"
                "2. Corré el agente service-requirements-scanner sobre los servicios que cambiaron para "
                "regenerar su requirements.json.\n"
                "3. Si el escáner deja issues, corré el agente requirements-notes-resolver para resolverlos "
                "(si queda alguno que no se puede resolver, dejalo anotado y seguí).\n"
                "4. Commit con el mensaje exacto \"Version release\" y push a staging (sin force). El PR se "
                "actualiza solo.\n"
                "No mergees ni cierres el PR. Al terminar resumí qué versiones subiste, qué issues quedaron "
                "abiertos y el SHA del commit."),
}


def title(skill: str) -> str:
    return f"{SKILLS.get(skill, skill)} (skill {skill})"


def _script(prompt: str, session: str) -> str:
    claude = '"$HOME/.local/bin/claude"'
    return "\n".join([
        f'test -x {claude} || {{ echo "Claude Code no está instalado en la VM (Herramientas → Claude Remote Control, paso 1)"; exit 1; }}',
        'test -s "$HOME/.claude/.credentials.json" || '
        '{ echo "Falta iniciar sesión en Claude en la VM (Claude Remote Control, paso 2)"; exit 1; }',
        # Como root, Claude solo acepta --dangerously-skip-permissions con IS_SANDBOX=1.
        '[ "$(id -u)" != 0 ] || export IS_SANDBOX=1',
        'cd "$CLAUDE_DIR" 2>/dev/null || cd "$HOME"',
        'echo "Claude está trabajando; el resultado aparece al terminar."',
        f"timeout {SKILL_TIMEOUT} {claude} -p --dangerously-skip-permissions {shlex.quote(prompt)} {session} </dev/null",
    ])


async def _check_branches(skill: str, owner: str, name: str, number: int) -> None:
    """El PR tiene que estar abierto y ir de la rama que pide la skill a la que corresponde."""
    head, base = _PR_BRANCHES[skill]
    _, pr = await github_service.rest("GET", f"/repos/{owner}/{name}/pulls/{number}")
    if pr.get("state") != "open":
        raise ServiceError("El PR no está abierto")
    if (pr["head"]["ref"], pr["base"]["ref"]) != (head, base):
        raise ServiceError(f"La skill {skill} solo corre sobre un PR {head} → {base}")


async def start(db: AsyncSession, actor: Actor, vm_id: int, skill: str, card_id: int, pr_url: str) -> dict:
    if skill not in SKILLS:
        raise ServiceError(f"Skill desconocida: {skill}")
    card = await board_service.get_card(db, card_id)
    if card.archived_at:
        raise ServiceError(f"La tarjeta #{card_id} está archivada")
    parsed = github_service.parse_pr_url(pr_url)
    if not parsed or pr_url not in (card.pr_urls or []):
        raise ServiceError("El PR tiene que ser un link de GitHub registrado en la tarjeta")
    owner, name, number = parsed
    url = f"https://github.com/{owner}/{name}/pull/{number}"
    if skill in _PR_BRANCHES:
        await _check_branches(skill, owner, name, number)

    vm = await vm_service.get_vm(db, actor, vm_id)
    vm_service.ensure_not_self(vm)
    running = (await db.execute(select(VmTask.id).where(VmTask.vm_id == vm.id, VmTask.status == "running"))).first()
    if running:
        raise ConflictError(f"Ya hay una tarea corriendo en {vm.name}; esperá a que termine")
    session_id = str(uuid.uuid4())
    task = VmTask(user_id=actor.user.id, vm_id=vm.id, issue=f"{TASK_PREFIX}{skill}", status="running", log="",
                  session_id=session_id)
    db.add(task)
    await db.commit()
    await board_service.add_comment(
        db, actor, card_id, f"▶ Se lanzó la skill `{skill}` con Claude en la VM **{vm.name}** sobre {url}")
    _launch(task.id, vm.id, skill, _PROMPTS[skill].format(skill=skill, url=url), f"--session-id {session_id}",
            header=f"▶ {title(skill)} en {vm.name} ({vm.username}@{vm.host})\n{url}\n")
    return vm_doctor_service.task_dict(task)


async def reply(db: AsyncSession, actor: Actor, task_id: int, text: str) -> dict:
    """Un turno más de la conversación con Claude: retoma la sesión de la tarea con el mensaje del usuario."""
    task = await db.get(VmTask, task_id)
    if not task or task.user_id != actor.user.id:
        raise NotFoundError("Tarea no encontrada")
    skill = task.issue.removeprefix(TASK_PREFIX)
    if not task.issue.startswith(TASK_PREFIX) or skill not in SKILLS or not task.session_id:
        raise ServiceError("Esta tarea no admite respuestas")
    text = (text or "").strip()
    if not text:
        raise ServiceError("Escribí un mensaje")
    if len(text) > REPLY_MAX_LENGTH:
        raise ServiceError(f"El mensaje es muy largo (máximo {REPLY_MAX_LENGTH} caracteres)")
    vm = await vm_service.get_vm(db, actor, task.vm_id)
    vm_service.ensure_not_self(vm)
    running = (await db.execute(select(VmTask.id).where(VmTask.vm_id == vm.id, VmTask.status == "running"))).first()
    if running:
        raise ConflictError(f"Ya hay una tarea corriendo en {vm.name}; esperá a que termine")
    task.status, task.finished_at = "running", None
    task.log += f"\n▶ Vos: {text}\n"
    await db.commit()
    _launch(task.id, vm.id, skill, text, f"--resume {task.session_id}")
    return vm_doctor_service.task_dict(task)


def _launch(task_id: int, vm_id: int, skill: str, prompt: str, session: str, header: str = "") -> None:
    job = asyncio.create_task(_run(task_id, vm_id, skill, prompt, session, header))
    vm_doctor_service._tasks.add(job)
    job.add_done_callback(vm_doctor_service._tasks.discard)


async def _run(task_id: int, vm_id: int, skill: str, prompt: str, session: str, header: str) -> None:
    async with AsyncSessionLocal() as db:
        vm = await db.get(TestVm, vm_id)
        password = vm_service.connect_options(vm).get("password")
        previous = (await db.get(VmTask, task_id)).log  # los turnos siguen en el mismo log
    log = JobLog(VmTask, task_id, [password or ""])
    log.text = previous
    done = {"finished_at": None}
    try:
        if header:
            await log.write(header)
        async with await vm_doctor_service._connect(vm) as conn:
            script = chr(10).join([vm_doctor_service._ROOT, vm_doctor_service._vm_env(vm), "set -e",
                                   _script(prompt, session)])
            code = await run_remote(log, conn, f"sh -c {shlex.quote(script)}", SKILL_TIMEOUT + 60)
        if code != 0:
            raise ServiceError("Claude terminó con código %d%s" % (code, " (se agotó el tiempo)" if code == 124 else ""))
        done["finished_at"] = datetime.now(timezone.utc)
        await log.write(f"\n✔ {title(skill)}: listo\n", status="success", **done)
    except ServiceError as exc:
        done["finished_at"] = datetime.now(timezone.utc)
        await log.write(f"\n✖ {exc.message}\n", status="failed", **done)
    except Exception as exc:  # noqa: BLE001 — cualquier otra cosa también tiene que cerrar la tarea
        logger.exception("Skill %s (tarea %s) falló", skill, task_id)
        done["finished_at"] = datetime.now(timezone.utc)
        await log.write(f"\n✖ Error inesperado: {exc}\n", status="failed", **done)
