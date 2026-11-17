"""Cargar la rama de un PR en la VM de pruebas de un usuario.

Pasos (cada carga es una fila de `preview_loads`, con su log):
  1. fetch:   optidev trae la rama a un mirror local (PREVIEW_MIRROR_DIR) con
              GITHUB_TOKEN. El token va por variables de entorno de git, no por
              la línea de comandos (no aparece en `ps`) ni en la URL.
  2. send:    arma un `git bundle` de la rama y lo sube a la VM por SFTP, con
              las credenciales de la VM del usuario. **Ninguna credencial de
              GitHub llega a la VM.**
  3. checkout: en la VM, PREVIEW_REMOTE_DIR/<repo>: fetch del bundle, checkout
              del commit exacto y `git clean -fd` (conserva los ignorados, como
              .env o node_modules).
  4. recipe:  corre la receta del repo (Repository.preview_script, la editan los
              admins) dentro del checkout, con PREVIEW_* en el entorno.

Corre en una tarea de fondo del proceso del backend (hay uno solo, ver
README). Si el backend se reinicia a mitad de una carga, al arrancar la carga
queda en `failed` (`fail_interrupted`).
"""

import asyncio
import base64
import json
import logging
import os
import posixpath
import re
import shlex
import tempfile
import time
from datetime import datetime, timezone

import asyncssh
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config.database import AsyncSessionLocal
from src.config.settings import settings
from src.models.preview_load import PreviewLoad
from src.models.repository import Repository
from src.services import preview_env_service, vm_doctor_service, vm_service
from src.services.auth_service import Actor
from src.services.errors import ConflictError, NotFoundError, ServiceError
from src.services.github_service import parse_github_url
from src.services.remote_job import JobLog, run_remote
from src.services.serializers import _iso

logger = logging.getLogger(__name__)

# Nombres de rama que se aceptan (además de `git check-ref-format`): sin
# espacios ni caracteres de shell. Todo va además con shlex.quote.
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")

# Tareas de fondo vivas (asyncio solo guarda referencias débiles).
_tasks: set[asyncio.Task] = set()


def load_dict(load: PreviewLoad, with_log: bool = False) -> dict:
    data = {
        "id": load.id,
        "vmId": load.vm_id,
        "vmName": load.vm.name if load.vm else None,
        "repository": load.repository.key if load.repository else None,
        "cardId": load.card_id,
        "prUrl": load.pr_url,
        "branch": load.branch,
        "sha": load.sha,
        "status": load.status,
        "step": load.step,
        "url": _preview_url(load),
        "createdAt": _iso(load.created_at),
        "finishedAt": _iso(load.finished_at),
    }
    if with_log:
        data["log"] = load.log
    if load.status == "failed":
        # Problemas conocidos (Docker sin instalar, sin disco...) con su arreglo.
        data["issues"] = vm_doctor_service.issues_in_log(load.log)
    return data


def _preview_url(load: PreviewLoad) -> str | None:
    template = load.repository.preview_url if load.repository else None
    if not template or not load.vm:
        return None
    return template.replace("{host}", load.vm.host)


def _load_query():
    return select(PreviewLoad).options(selectinload(PreviewLoad.vm), selectinload(PreviewLoad.repository),
                                       selectinload(PreviewLoad.user))


async def get_load(db: AsyncSession, actor: Actor, load_id: int) -> dict:
    load = (await db.execute(_load_query().where(PreviewLoad.id == load_id))).scalar_one_or_none()
    if not load or load.user_id != actor.user.id:
        raise NotFoundError("Carga no encontrada")
    return load_dict(load, with_log=True)


async def current_loads(db: AsyncSession, actor: Actor) -> list[dict]:
    """La última carga de cada (VM, repo) del usuario: qué tiene cargado cada VM."""
    latest = (
        select(func.max(PreviewLoad.id))
        .where(PreviewLoad.user_id == actor.user.id)
        .group_by(PreviewLoad.vm_id, PreviewLoad.repository_id)
    )
    result = await db.execute(_load_query().where(PreviewLoad.id.in_(latest)).order_by(PreviewLoad.id.desc()))
    return [load_dict(load) for load in result.scalars() if load.vm and not load.vm.deleted_at]


async def start_load(db: AsyncSession, actor: Actor, data: dict) -> dict:
    repo = (await db.execute(
        select(Repository).where(Repository.deleted_at.is_(None), Repository.key == data["repository"])
    )).scalar_one_or_none()
    if not repo:
        raise NotFoundError(f"Repositorio {data['repository']} no encontrado")
    if not (repo.preview_script or "").strip():
        raise ServiceError(f"{repo.key} todavía no tiene receta para cargarse en una VM (la configura un admin)")
    if not parse_github_url(repo.github_url):
        raise ServiceError(f"{repo.key} no tiene una URL de GitHub válida")
    branch = (data.get("branch") or "").strip()
    if not _BRANCH.match(branch) or ".." in branch or branch.startswith(("-", "/")) or branch.endswith((".lock", "/")):
        raise ServiceError("Nombre de rama inválido")
    vm = await vm_service.get_vm(db, actor, data["vmId"])
    vm_service.ensure_not_self(vm)

    running = (await db.execute(select(PreviewLoad.id).where(
        PreviewLoad.vm_id == vm.id, PreviewLoad.repository_id == repo.id, PreviewLoad.status == "running",
    ))).first()
    if running:
        raise ConflictError(f"Ya se está cargando {repo.key} en {vm.name}; esperá a que termine")

    load = PreviewLoad(user_id=actor.user.id, vm_id=vm.id, repository_id=repo.id, branch=branch,
                       card_id=data.get("cardId"), pr_url=data.get("prUrl"), status="running", step="fetch",
                       log="")
    db.add(load)
    await db.commit()
    task = asyncio.create_task(_run(load.id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    load = (await db.execute(_load_query().where(PreviewLoad.id == load.id))).scalar_one()
    return load_dict(load, with_log=True)


async def fail_interrupted() -> None:
    """Al arrancar: las cargas que quedaron `running` las cortó un reinicio."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PreviewLoad).where(PreviewLoad.status == "running"))
        for load in result.scalars():
            load.status = "failed"
            load.log += "\n[optidev se reinició durante la carga; volvé a intentarlo]\n"
            load.finished_at = datetime.now(timezone.utc)
        await db.commit()


def _git_env(token: str | None) -> dict:
    # Sin la config global ni la del sistema: así no se cuela otra credencial
    # (p. ej. el helper de `gh` de root) y el acceso es solo el de GITHUB_TOKEN.
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    return env


def _remote_url(repo: Repository) -> str:
    owner, name = parse_github_url(repo.github_url)
    return f"https://github.com/{owner}/{name}.git"


async def _git(log: JobLog, env: dict, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec("git", *args, env=env, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    text = out.decode(errors="replace")
    if proc.returncode != 0:
        await log.write(text)
        raise ServiceError(f"git {args[0]} falló (código {proc.returncode})")
    return text


async def _requirements(log: JobLog, env: dict, mirror: str, sha: str) -> list[tuple[str, dict]]:
    """Los requirements.json del commit (la misma definición que lee Helios)."""
    paths = [p for p in (await _git(log, env, "-C", mirror, "ls-tree", "-r", "--name-only", sha)).splitlines()
             if posixpath.basename(p) == "requirements.json"]
    found = []
    for path in paths:
        try:
            found.append((path, json.loads(await _git(log, env, "-C", mirror, "show", f"{sha}:{path}"))))
        except ValueError:
            await log.write(f"⚠ {path} no es JSON válido; se ignora\n")
    return found


async def _write_env_files(log: JobLog, conn, env: dict, mirror: str, sha: str, repo_key: str, workdir: str,
                           username: str, load_id: int) -> None:
    """Arma el .env de cada servicio declarado en requirements.json: valores de
    preview/<repo>/<servicio> en el Vault > ambiente del usuario (su base y su
    mount) > defaultValue. Nunca se escriben valores en el log."""
    requirements = [(p, r) for p, r in await _requirements(log, env, mirror, sha)
                    if (r.get("environment") or {}).get("envFile")]
    if not requirements:
        return
    await log.step("env", "Configurando los .env de los servicios")
    try:
        context = await preview_env_service.ensure_user(username, log)
    except ServiceError as exc:
        if exc.status_code == 409:  # Vault de preview sin conectar
            await log.write(f"⚠ {exc.message}: los .env no se arman\n")
            return
        raise
    context["VAULT_SECRET_ID"] = await preview_env_service.new_service_secret(
        username, {"optidev_load": str(load_id), "repo": repo_key})
    log.add_secrets(context.values())
    async with conn.start_sftp_client() as sftp:
        for path, req in requirements:
            service = req.get("service") or posixpath.basename(posixpath.dirname(path))
            environment = req["environment"]
            target = posixpath.normpath(posixpath.join(workdir, environment["envFile"]))
            if not target.startswith(workdir + "/"):
                await log.write(f"⚠ {service}: envFile fuera del repo ({environment['envFile']}); se ignora\n")
                continue
            values = await preview_env_service.service_values(repo_key, service)
            log.add_secrets(values.values())
            env_map, origin, missing = preview_env_service.compute_env(
                environment.get("variables") or [], values, context)
            await sftp.makedirs(posixpath.dirname(target), exist_ok=True)
            async with sftp.open(target, "w") as f:
                await f.write(preview_env_service.render_env(env_map))
            await sftp.chmod(target, 0o600)
            await log.write(f"{service}: {len(env_map)} variables ({origin['vault']} del Vault, {origin['usuario']} "
                            f"de tu ambiente, {origin['default']} por defecto) → {environment['envFile']}\n")
            if missing:
                await log.write(f"  ⚠ faltan obligatorias: {', '.join(missing)} "
                                f"(cargalas en el Vault: preview/{repo_key}/{service})\n")


async def _run(load_id: int) -> None:
    async with AsyncSessionLocal() as db:
        load = (await db.execute(_load_query().where(PreviewLoad.id == load_id))).scalar_one()
        repo, vm, branch, username = load.repository, load.vm, load.branch, load.user.username
        options = vm_service.connect_options(vm)
    log = JobLog(PreviewLoad, load_id, [settings.GITHUB_TOKEN or "", options.get("password") or ""])
    mirror = os.path.abspath(os.path.join(settings.PREVIEW_MIRROR_DIR, f"{repo.key}.git"))
    workdir = f"{settings.PREVIEW_REMOTE_DIR}/{repo.key}"
    bundle_remote = f"/tmp/optidev-{repo.key}-{load_id}.bundle"
    started = time.monotonic()
    try:
        await log.step("fetch", f"Descargando {branch} de {repo.key}")
        env = _git_env(settings.GITHUB_TOKEN)
        if not os.path.isdir(mirror):
            os.makedirs(os.path.dirname(mirror), exist_ok=True)
            await _git(log, env, "init", "--bare", "-q", mirror)
        await _git(log, env, "-C", mirror, "fetch", "--no-tags", "-q", _remote_url(repo),
                   f"+refs/heads/{branch}:refs/heads/{branch}")
        sha = (await _git(log, env, "-C", mirror, "rev-parse", f"refs/heads/{branch}")).strip()
        await log.write(f"Commit {sha}\n", sha=sha)

        await log.step("send", f"Enviando a {vm.name} ({vm.username}@{vm.host})")
        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "branch.bundle")
            await _git(log, env, "-C", mirror, "bundle", "create", "-q", bundle, f"refs/heads/{branch}")
            try:
                conn = await asyncssh.connect(**options)
            except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
                raise ServiceError(vm_service.connection_error(vm, exc)) from exc
            async with conn:
                async with AsyncSessionLocal() as db:
                    stored = await db.get(type(vm), vm.id)
                    if vm_service.remember_host_key(stored, conn):
                        await db.commit()
                async with conn.start_sftp_client() as sftp:
                    await sftp.put(bundle, bundle_remote)
                await log.write(f"Bundle de {os.path.getsize(bundle) // 1024} KB enviado\n")

                await log.step("checkout", f"Checkout en {workdir}")
                q = shlex.quote
                checkout = " && ".join([
                    "set -e",
                    "command -v git >/dev/null || { echo 'La VM no tiene git instalado'; exit 127; }",
                    f"mkdir -p {q(workdir)}", f"cd {q(workdir)}",
                    "{ [ -d .git ] || git init -q; }",
                    f"git fetch -q {q(bundle_remote)} {q(f'+refs/heads/{branch}:refs/remotes/optidev/{branch}')}",
                    f"git checkout -q -f --detach {q(sha)}",
                    "git clean -fdq",
                    f"rm -f {q(bundle_remote)}",
                    "git log -1 --format='%h %s (%an)'",
                ])
                if await run_remote(log, conn, f"sh -c {q(checkout)}", 300) != 0:
                    raise ServiceError("Falló el checkout en la VM")

                await _write_env_files(log, conn, env, mirror, sha, repo.key, workdir, username, load_id)

                await log.step("recipe", "Ejecutando la receta del repo")
                recipe_path = f"{settings.PREVIEW_REMOTE_DIR}/.recipes/{repo.key}.sh"
                async with conn.start_sftp_client() as sftp:
                    await sftp.makedirs(os.path.dirname(recipe_path), exist_ok=True)
                    async with sftp.open(recipe_path, "w") as f:
                        await f.write(repo.preview_script.replace("\r\n", "\n"))
                env_vars = {"PREVIEW_REPO": repo.key, "PREVIEW_BRANCH": branch, "PREVIEW_SHA": sha,
                            "PREVIEW_DIR": workdir, "PREVIEW_HOST": vm.host}
                exports = " ".join(f"{k}={q(v)}" for k, v in env_vars.items())
                timeout = settings.PREVIEW_TIMEOUT_SECONDS - (time.monotonic() - started)
                code = await run_remote(log, conn, f"cd {q(workdir)} && env {exports} bash -e {q(recipe_path)}",
                                     max(timeout, 60))
                if code != 0:
                    raise ServiceError(f"La receta terminó con código {code}")

        await log.write(f"\n✔ {repo.key} {branch} cargado en {vm.name}\n", force=True, status="success",
                        step="done", finished_at=datetime.now(timezone.utc))
    except ServiceError as exc:
        await log.write(f"\n✖ {exc.message}\n", force=True, status="failed", finished_at=datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 — cualquier otra cosa también tiene que cerrar la carga
        logger.exception("Carga %s falló", load_id)
        await log.write(f"\n✖ Error inesperado: {exc}\n", force=True, status="failed",
                        finished_at=datetime.now(timezone.utc))
