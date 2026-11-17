"""Problemas conocidos de las VMs de pruebas: detectarlos y arreglarlos con un botón.

Cada problema del catálogo (`ISSUES`) tiene:
  - `check`: comando que sale con 0 si la VM está bien (lo usa el diagnóstico).
  - `patterns`: mensajes de error que lo delatan en el log de una carga fallida.
  - `fix`: comando que lo arregla (como root, o con `sudo -n` si el usuario SSH
    no es root).

**El cliente solo elige un id del catálogo**: nunca manda comandos. Así el
botón "Resolver" no sirve para ejecutar cualquier cosa en una VM.

Los arreglos pueden tardar minutos (instalar Docker): corren de fondo como
`VmTask`, con el log en vivo, igual que las cargas de ramas.

`group` separa el diagnóstico de las herramientas opcionales que usan el mismo
mecanismo (p. ej. dejar Claude Remote Control fijo en la VM, o la rutina diaria,
en Herramientas; o el marketplace de agentes, en Mi perfil): lo que no es del grupo "doctor" no aparece como problema de la VM.
"""

import asyncio
import logging
import re
import shlex
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import AsyncSessionLocal
from src.config.settings import settings
from src.models.api_token import ApiToken
from src.models.test_vm import TestVm
from src.models.vm_task import VmTask
from src.services import vm_service
from src.services.auth_service import Actor, generate_api_token, hash_api_token
from src.services.errors import ConflictError, NotFoundError, ServiceError
from src.services.remote_job import JobLog, run_remote
from src.services.serializers import _iso

logger = logging.getLogger(__name__)

DIAGNOSE_TIMEOUT = 60
FIX_TIMEOUT = 20 * 60
# `$S` = prefijo para lo que necesita root.
_ROOT = 'S=""; [ "$(id -u)" = 0 ] || S="sudo -n"; export DEBIAN_FRONTEND=noninteractive'
_APT = "$S apt-get update -q && $S apt-get install -y -q"


@dataclass(frozen=True)
class Issue:
    id: str
    title: str
    check: str
    fix_title: str | None = None
    fix: str | None = None
    patterns: tuple[str, ...] = field(default_factory=tuple)
    group: str = "doctor"
    # Para lo que no tiene arreglo automático: qué hacer a mano y, si se hace
    # en la terminal de la VM, el comando que el botón escribe ahí.
    hint: str | None = None
    command: str | None = None
    # El arreglo recibe un token MCP nuevo del usuario ($ODV_TOKEN). Es la única
    # forma de que un token llegue a la VM.
    mcp_token: bool = False


GROUPS = ("doctor", "claude_rc", "routine", "agents")

# Carpeta donde corre Remote Control en cada VM: /opt/claude/<nombre de la VM>.
# Claude Code agrupa las sesiones por el nombre de esa carpeta, así en la barra
# lateral se ve la VM (y no "work" o "root"). Los comandos la usan como
# "$CLAUDE_DIR", que fija `_vm_env` para cada VM.
CLAUDE_RC_BASE = "/opt/claude"

# Unidad de systemd de Remote Control. Corre como el usuario SSH (es quien tiene
# Claude instalado y la sesión iniciada). La TUI redibuja cada segundo: su salida
# va a /dev/null para no llenar el journal; los errores sí quedan ahí.
# Las sesiones corren sin pedir permisos (bypassPermissions): es una VM de pruebas.
# Claude no permite ese modo como root salvo con IS_SANDBOX=1 ($SANDBOX_ENV lo agrega
# solo si el usuario SSH es root): aceptable porque la VM es desechable.
_CLAUDE_RC_UNIT = """[Unit]
Description=Claude Code Remote Control
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$CLAUDE_DIR
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
$SANDBOX_ENV
ExecStart=$HOME/.local/bin/claude remote-control --permission-mode bypassPermissions
Restart=always
RestartSec=10
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target"""


def claude_dir_for(name: str, vm_id: int) -> str:
    slug = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:40].strip("-") or f"vm-{vm_id}"
    return f"{CLAUDE_RC_BASE}/{slug}"


def claude_dir(vm: TestVm) -> str:
    return claude_dir_for(vm.name, vm.id)


def _vm_env(vm: TestVm) -> str:
    return f"CLAUDE_DIR={shlex.quote(claude_dir(vm))}"


def _claude_rc_issues(remote: str) -> list[Issue]:
    claude = '"$HOME/.local/bin/claude"'
    credentials = 'test -s "$HOME/.claude/.credentials.json"'
    # Si la carpeta no está marcada como confiable, Claude lo pregunta al arrancar y el servicio no tiene
    # terminal para responder. Sin python3 no se puede leer ~/.claude.json: se da por buena.
    script = ('import json, os, sys; p = json.load(open(os.path.expanduser("~/.claude.json"))).get("projects", {}); '
              'sys.exit(0 if p.get(sys.argv[1], {}).get("hasTrustDialogAccepted") else 1)')
    trusted = f'! command -v python3 || python3 -c {shlex.quote(script)} "$CLAUDE_DIR"'
    # Las sesiones arrancan en $CLAUDE_DIR; los repos cargados están en otro lado.
    notes = (f"# VM de pruebas de optidev\n\nLos repos cargados desde optidev (\"Cargar en mi VM\") están en "
             f"`{settings.PREVIEW_REMOTE_DIR}/<repo>`, cada uno en la rama que se cargó.")
    # Si la VM se renombró en optidev, el servicio viejo corre en otra carpeta; si se instaló antes de
    # bypassPermissions, pide permisos. En los dos casos hay que reinstalarlo.
    service_ok = ('systemctl is-active --quiet claude-rc && '
                  '[ "$(systemctl show -p WorkingDirectory --value claude-rc)" = "$CLAUDE_DIR" ] && '
                  'grep -q -- "--permission-mode bypassPermissions" /etc/systemd/system/claude-rc.service')
    # bypassPermissions muestra un aviso la primera vez y el servicio no tiene terminal para aceptarlo:
    # se deja aceptado en ~/.claude/settings.json (sin pisar lo que ya tenga).
    accept = ('import json, os; p = os.path.expanduser("~/.claude/settings.json"); '
              'd = json.load(open(p)) if os.path.exists(p) else {}; d["skipDangerousModePermissionPrompt"] = True; '
              'os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(d, open(p, "w"), indent=2)')
    return [
        Issue("claude", "Claude Code instalado", f'test -x {claude} && test -w "$CLAUDE_DIR"',
              "Instalar Claude Code",
              "\n".join([f"command -v curl >/dev/null || {{ {_APT} curl; }}",
                         f'test -x {claude} || curl -fsSL https://claude.ai/install.sh | bash',
                         '$S mkdir -p "$CLAUDE_DIR" && $S chown "$(id -u):$(id -g)" "$CLAUDE_DIR"',
                         f'test -e "$CLAUDE_DIR/CLAUDE.md" || printf \'%s\\n\' {shlex.quote(notes)} '
                         '> "$CLAUDE_DIR/CLAUDE.md"']),
              group="claude_rc"),
        Issue("claude_login", "Sesión de Claude iniciada", f"{credentials} && ( {trusted} )",
              group="claude_rc",
              hint="Se abre Claude en la terminal de la VM: iniciá sesión con tu cuenta de claude.ai y aceptá la "
                   "carpeta como confiable. Al terminar, /exit y volvé a chequear.",
              command='cd "$CLAUDE_DIR" && ~/.local/bin/claude'),
        Issue("claude_rc", "Remote Control corriendo como servicio (claude-rc)", service_ok,
              "Instalar servicio",
              "\n".join([
                  f'test -x {claude} || {{ echo "Claude Code no está instalado (paso 1)"; exit 1; }}',
                  f'{credentials} || {{ echo "Falta iniciar sesión en Claude (paso 2)"; exit 1; }}',
                  'SANDBOX_ENV=""; [ "$(id -u)" != 0 ] || SANDBOX_ENV="Environment=IS_SANDBOX=1"',
                  f"command -v python3 >/dev/null || {{ {_APT} python3; }}",
                  f"python3 -c {shlex.quote(accept)}",
                  '$S mkdir -p "$CLAUDE_DIR"',
                  f"$S tee /etc/systemd/system/claude-rc.service >/dev/null <<EOF\n{_CLAUDE_RC_UNIT}\nEOF",
                  "$S systemctl daemon-reload",
                  "$S systemctl enable claude-rc",
                  "$S systemctl restart claude-rc",
                  "echo 'Esperando que arranque...'; sleep 8",
                  "$S systemctl is-active --quiet claude-rc || { $S journalctl -u claude-rc -n 30 --no-pager; exit 1; }",
                  'echo "Servicio activo en $CLAUDE_DIR: la VM aparece en claude.ai/code como \'${CLAUDE_DIR##*/}\'."',
              ]),
              group="claude_rc"),
    ]


# Rutina diaria: un timer de systemd en la VM corre Claude Code sin interacción
# con este prompt. No usa las Routines de claude.ai: esas no pueden apuntar a una
# VM de Remote Control. La tool report_routine_run le avisa a optidev que corrió.
ROUTINE_PROMPT = """Rutina diaria de optidev (tablero de tareas de Optidev).

1. Llamá a la tool `report_routine_run` del MCP optidev.
2. Con `whoami` identificá tu usuario y con `list_cards` (assignee = tu username) traé tus tarjetas de "Pendiente" y "En proceso". Leé cada una con `get_card`, comentarios incluidos, y mirá las capturas con `get_card_images` si las tiene.
3. Avanzá las que puedas resolver sin decisiones pendientes, con las reglas del tablero:
   - Al empezar, movela a "En proceso".
   - Trabajá en una rama nueva desde la rama base del repo (`get_board_info`) y corré los tests.
   - Abrí el PR contra esa rama base, registralo con `add_pr_url` y movela a "PR".
   - Nunca mergees un PR ni muevas una tarjeta a "Hecho".
4. Si a una tarjeta le falta información o tiene decisiones abiertas, dejá las preguntas con `add_comment` y movela a "En espera".
5. Al terminar, comentá en cada tarjeta que tocaste un resumen de lo que hiciste."""
ROUTINE_SCHEDULE = "Mon..Fri *-*-* 01:00:00 America/Santiago"
ROUTINE_PROMPT_FILE = "/etc/optidev-rutina/prompt.md"
# Nombre del token MCP que se crea para la rutina; al reinstalar se revoca el anterior.
ROUTINE_TOKEN_NAME = "Rutina diaria ({vm})"

# Corre sin pedir permisos (crea ramas, corre tests, abre PRs): como root, Claude
# lo exige con IS_SANDBOX=1. `$$` es un `$` literal para systemd; el `\$\$` es
# para que el heredoc no lo expanda.
_ROUTINE_SERVICE = f"""[Unit]
Description=Rutina diaria de optidev (Claude Code)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$(id -un)
WorkingDirectory=$CLAUDE_DIR
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
$SANDBOX_ENV
ExecStart=/bin/sh -c 'exec claude -p --dangerously-skip-permissions "\\$\\$(cat {ROUTINE_PROMPT_FILE})"'
TimeoutStartSec=4h
StandardOutput=journal
StandardError=journal"""

# Persistent=false: si la VM estaba apagada a la hora, no corre al prender.
_ROUTINE_TIMER = f"""[Unit]
Description=Rutina diaria de optidev (lunes a viernes, 1:00 hora de Chile)

[Timer]
OnCalendar={ROUTINE_SCHEDULE}
Persistent=false

[Install]
WantedBy=timers.target"""


def _routine_issues() -> list[Issue]:
    claude = '"$HOME/.local/bin/claude"'
    ready = "\n".join([
        f'test -x {claude} || {{ echo "Claude Code no está instalado (Claude Remote Control, paso 1)"; exit 1; }}',
        'test -s "$HOME/.claude/.credentials.json" || '
        '{ echo "Falta iniciar sesión en Claude (Claude Remote Control, paso 2)"; exit 1; }',
    ])
    # El MCP tiene que ser el de este optidev ($ODV_URL) con el token creado para esta VM ($ODV_PREFIX, vacío
    # si no hay uno vigente): otro optidev (prod vs. preview) u otro token registraría la corrida en otro lado.
    # `mcp get` además prueba la conexión: con un token revocado sale "Failed to connect".
    script = ('import json, os, sys; url, prefix = sys.argv[1:]; '
              's = json.load(open(os.path.expanduser("~/.claude.json"))).get("mcpServers", {}).get("optidev", {}); '
              'auth = s.get("headers", {}).get("Authorization", ""); '
              'sys.exit(0 if prefix and s.get("url") == url and auth.startswith("Bearer " + prefix) else 1)')
    mcp_ok = (f'python3 -c {shlex.quote(script)} "$ODV_URL/mcp" "$ODV_PREFIX" && '
              f"{claude} mcp get optidev 2>/dev/null | grep -q Connected")
    routine_ok = " && ".join([
        "systemctl is-enabled --quiet optidev-rutina.timer",
        "systemctl is-active --quiet optidev-rutina.timer",
        '[ "$(systemctl show -p WorkingDirectory --value optidev-rutina.service)" = "$CLAUDE_DIR" ]',
        f'[ "$(cat {ROUTINE_PROMPT_FILE} 2>/dev/null)" = {shlex.quote(ROUTINE_PROMPT)} ]',
    ])
    probe = "Llamá a la tool report_routine_run del MCP optidev y no hagas nada más."
    return [
        Issue("routine_mcp", "MCP de optidev conectado en Claude", mcp_ok, "Conectar MCP",
              "\n".join([
                  ready,
                  f"command -v python3 >/dev/null || {{ {_APT} python3; }}",
                  f"{claude} mcp remove optidev --scope user >/dev/null 2>&1 || true",
                  f'{claude} mcp add --transport http --scope user optidev "$ODV_URL/mcp" '
                  '--header "Authorization: Bearer $ODV_TOKEN"',
              ]),
              group="routine", mcp_token=True),
        Issue("routine", "Rutina diaria instalada (lunes a viernes, 1:00)", routine_ok, "Instalar rutina",
              "\n".join([
                  ready,
                  f'{mcp_ok} || {{ echo "Falta conectar el MCP de optidev (paso anterior)"; exit 1; }}',
                  'SANDBOX_ENV=""; [ "$(id -u)" != 0 ] || SANDBOX_ENV="Environment=IS_SANDBOX=1"',
                  f'$S mkdir -p "$CLAUDE_DIR" {shlex.quote(ROUTINE_PROMPT_FILE.rsplit("/", 1)[0])}',
                  f"printf '%s\\n' {shlex.quote(ROUTINE_PROMPT)} | $S tee {ROUTINE_PROMPT_FILE} >/dev/null",
                  f"$S tee /etc/systemd/system/optidev-rutina.service >/dev/null <<EOF\n{_ROUTINE_SERVICE}\nEOF",
                  f"$S tee /etc/systemd/system/optidev-rutina.timer >/dev/null <<EOF\n{_ROUTINE_TIMER}\nEOF",
                  "$S systemctl daemon-reload",
                  "$S systemctl enable optidev-rutina.timer",
                  "$S systemctl restart optidev-rutina.timer",
                  "$S systemctl list-timers optidev-rutina.timer --no-pager",
                  # Prueba de punta a punta (sesión de Claude + MCP) sin tocar tarjetas: solo avisa que corrió.
                  "echo 'Probando: Claude le avisa a optidev que la rutina quedó instalada...'",
                  # El prompt va antes de --allowedTools: ese flag toma varios valores y se lo comería.
                  f'cd "$CLAUDE_DIR" && timeout 300 {claude} -p {shlex.quote(probe)} '
                  "--allowedTools mcp__optidev__report_routine_run </dev/null",
              ]),
              group="routine"),
    ]


# Marketplace de agentes y skills de Optidev (repo privado: la VM necesita acceso a GitHub). Todas las
# VMs tienen que tenerlo con todos sus plugins instalados y al día (p. ej. la skill revisar-pr, que
# skill_service corre con `claude -p`). El mismo script lo corre el arreglo (instala y actualiza a mano,
# desde Mi perfil) y el timer nocturno. Es idempotente: agrega el marketplace si falta, lo actualiza e
# instala o actualiza cada plugin que declara. Los plugins se leen del marketplace.json clonado, así que
# un plugin nuevo del repo se instala solo en la siguiente corrida.
AGENTS_REPO = "fmaturanaa/optidev-agents"
AGENTS_MARKETPLACE = "optidev-tools"  # `name` de su .claude-plugin/marketplace.json
AGENTS_UPDATE_SCRIPT_FILE = "/usr/local/bin/optidev-agents-update"
AGENTS_UPDATE_SCHEDULE = "*-*-* 00:30:00 America/Santiago"
AGENTS_MAX_AGE_HOURS = 36  # pasado ese tiempo sin actualizar, se avisa que está desactualizado
_AGENTS_MANIFEST = f"$HOME/.claude/plugins/marketplaces/{AGENTS_MARKETPLACE}/.claude-plugin/marketplace.json"
_AGENTS_PLUGINS = ('import json, os, sys; m = json.load(open(os.path.expanduser(sys.argv[1]))); '
                   'print(*[p["name"] for p in m["plugins"]])')
AGENTS_UPDATE_SCRIPT = f"""#!/bin/sh
set -e
claude="$HOME/.local/bin/claude"
test -x "$claude" || {{ echo "Claude Code no está instalado en la VM"; exit 1; }}
"$claude" plugin marketplace list --json | grep -q '"{AGENTS_MARKETPLACE}"' \\
  || "$claude" plugin marketplace add {AGENTS_REPO}
"$claude" plugin marketplace update {AGENTS_MARKETPLACE}
for plugin in $(python3 -c {shlex.quote(_AGENTS_PLUGINS)} "{_AGENTS_MANIFEST}"); do
  if "$claude" plugin list --json | grep -q "\\\"$plugin@{AGENTS_MARKETPLACE}\\\""; then
    "$claude" plugin update "$plugin@{AGENTS_MARKETPLACE}"
  else
    "$claude" plugin install "$plugin@{AGENTS_MARKETPLACE}"
  fi
done"""

_AGENTS_SERVICE = f"""[Unit]
Description=Actualiza el marketplace de agentes y skills de Optidev (Claude Code)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$(id -un)
Environment=HOME=$HOME
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart={AGENTS_UPDATE_SCRIPT_FILE}
TimeoutStartSec=30min
StandardOutput=journal
StandardError=journal"""

# Antes de la rutina diaria (1:00): que corra con las skills al día. Persistent=true: si la VM estaba
# apagada a la hora, actualiza al prender.
_AGENTS_TIMER = f"""[Unit]
Description=Actualización nocturna de agentes y skills de Optidev (0:30 hora de Chile)

[Timer]
OnCalendar={AGENTS_UPDATE_SCHEDULE}
Persistent=true

[Install]
WantedBy=timers.target"""


def _agents_issues() -> list[Issue]:
    claude = '"$HOME/.local/bin/claude"'
    ready = "\n".join([
        f'test -x {claude} || {{ echo "Claude Code no está instalado (Claude Remote Control, paso 1)"; exit 1; }}',
        'test -s "$HOME/.claude/.credentials.json" || '
        '{ echo "Falta iniciar sesión en Claude (Claude Remote Control, paso 2)"; exit 1; }',
        f"command -v python3 >/dev/null || {{ {_APT} python3; }}",
        "command -v git >/dev/null || { " + _APT + " git; }",
    ])
    # Todos los plugins del marketplace.json clonado figuran como instalados.
    installed = (f'test -f "{_AGENTS_MANIFEST}" && plugins="$(python3 -c {shlex.quote(_AGENTS_PLUGINS)} '
                 f'"{_AGENTS_MANIFEST}")" && [ -n "$plugins" ] && list="$({claude} plugin list --json)" && '
                 f'for p in $plugins; do echo "$list" | grep -q "\\\"$p@{AGENTS_MARKETPLACE}\\\"" || exit 1; done')
    # Al día: `marketplace update` corrió en las últimas 36 h (si no, la actualización nocturna falló o la VM
    # estuvo apagada) y, si el clon es un repo git, no hay commits nuevos en GitHub. Sin red no se alerta.
    fresh = ('import json, os, sys, time; from datetime import datetime; '
             'm = json.load(open(os.path.expanduser("~/.claude/plugins/known_marketplaces.json")))[sys.argv[1]]; '
             'sys.exit(0 if time.time() - datetime.fromisoformat(m["lastUpdated"].replace("Z", "+00:00")).timestamp() '
             f'< {AGENTS_MAX_AGE_HOURS} * 3600 else 1)')
    mp_dir = f"$HOME/.claude/plugins/marketplaces/{AGENTS_MARKETPLACE}"
    current = (f'python3 -c {shlex.quote(fresh)} {AGENTS_MARKETPLACE} && {{ [ ! -d "{mp_dir}/.git" ] || '
               f'! timeout 20 git -C "{mp_dir}" fetch -q 2>/dev/null || '
               f'[ "$(git -C "{mp_dir}" rev-parse HEAD)" = "$(git -C "{mp_dir}" rev-parse FETCH_HEAD)" ]; }}')
    timer_ok = " && ".join([
        "systemctl is-enabled --quiet optidev-agentes.timer",
        "systemctl is-active --quiet optidev-agentes.timer",
        f'[ "$(cat {AGENTS_UPDATE_SCRIPT_FILE} 2>/dev/null)" = {shlex.quote(AGENTS_UPDATE_SCRIPT)} ]',
    ])
    return [
        # En Mi perfil el arreglo de este paso se ofrece siempre ("Instalar / actualizar ahora"), esté ok o no.
        Issue("agents_marketplace", "Agentes y skills de Optidev instalados (todos los plugins)", installed,
              "Instalar / actualizar ahora",
              "\n".join([ready, AGENTS_UPDATE_SCRIPT]),
              group="agents"),
        Issue("agents_current", "Agentes y skills al día", current, "Actualizar ahora",
              "\n".join([ready, AGENTS_UPDATE_SCRIPT]), group="agents"),
        Issue("agents_timer", "Actualización automática instalada (todas las noches, 0:30)", timer_ok,
              "Instalar actualización automática",
              "\n".join([
                  ready,
                  f"$S tee {AGENTS_UPDATE_SCRIPT_FILE} >/dev/null <<'ODV_EOF'\n{AGENTS_UPDATE_SCRIPT}\nODV_EOF",
                  f"$S chmod +x {AGENTS_UPDATE_SCRIPT_FILE}",
                  f"$S tee /etc/systemd/system/optidev-agentes.service >/dev/null <<EOF\n{_AGENTS_SERVICE}\nEOF",
                  f"$S tee /etc/systemd/system/optidev-agentes.timer >/dev/null <<EOF\n{_AGENTS_TIMER}\nEOF",
                  "$S systemctl daemon-reload",
                  "$S systemctl enable optidev-agentes.timer",
                  "$S systemctl restart optidev-agentes.timer",
                  "$S systemctl list-timers optidev-agentes.timer --no-pager",
              ]),
              group="agents"),
    ]


def _issues() -> list[Issue]:
    remote = shlex.quote(settings.PREVIEW_REMOTE_DIR)
    return [
        Issue("root", "El usuario SSH no es root ni tiene sudo sin contraseña",
              'test "$(id -u)" = 0 || sudo -n true',
              patterns=(r"sudo: a password is required", r"sudo: .*not found")),
        Issue("git", "git no está instalado", "command -v git",
              "Instalar git", f"{_APT} git",
              (r"La VM no tiene git instalado", r"git: (command )?not found")),
        Issue("docker", "Docker no está instalado", "command -v docker",
              "Instalar Docker",
              f"{_APT} ca-certificates curl && curl -fsSL https://get.docker.com | $S sh "
              "&& $S systemctl enable --now docker",
              (r"docker: (command )?not found",)),
        Issue("compose", "Falta Docker Compose", "! command -v docker || docker compose version",
              "Instalar Docker Compose",
              f"{{ {_APT} docker-compose-plugin; }} || {{ {_APT} ca-certificates curl "
              f"&& curl -fsSL https://get.docker.com | $S sh; }}",
              (r"'compose' is not a docker command", r"unknown command.*compose")),
        Issue("docker_daemon", "Docker no está corriendo", "! command -v docker || docker info",
              "Arrancar Docker", "$S systemctl enable --now docker",
              (r"Cannot connect to the Docker daemon",)),
        Issue("disk", "Queda poco disco (más del 90 % usado)",
              '[ "$(df --output=pcent / | tail -1 | tr -dc 0-9)" -lt 90 ]',
              "Liberar espacio de Docker", "! command -v docker || $S docker system prune -af",
              (r"[Nn]o space left on device",)),
        Issue("preview_dir", f"No se puede escribir en {settings.PREVIEW_REMOTE_DIR}",
              f"mkdir -p {remote} && test -w {remote}",
              "Dar permisos", f'$S mkdir -p {remote} && $S chown "$(id -u):$(id -g)" {remote}',
              (rf"mkdir: cannot create directory .{re.escape(settings.PREVIEW_REMOTE_DIR)}",)),
        *_claude_rc_issues(remote),
        *_routine_issues(),
        *_agents_issues(),
    ]


def _issue(issue_id: str) -> Issue:
    issue = next((i for i in _issues() if i.id == issue_id), None)
    if not issue:
        raise NotFoundError("Problema desconocido")
    return issue


def _issue_dict(issue: Issue, ok: bool | None = None, vm: TestVm | None = None) -> dict:
    command = issue.command
    if command and vm:
        command = command.replace('"$CLAUDE_DIR"', shlex.quote(claude_dir(vm)))
    data = {"id": issue.id, "title": issue.title, "fixTitle": issue.fix_title, "hint": issue.hint,
            "command": command}
    if ok is not None:
        data["ok"] = ok
    return data


def issues_in_log(log: str) -> list[dict]:
    """Problemas conocidos que aparecen en el log de una carga fallida."""
    return [_issue_dict(i) for i in _issues() if i.group == "doctor" and any(re.search(p, log or "") for p in i.patterns)]


def task_dict(task: VmTask) -> dict:
    issue = next((i for i in _issues() if i.id == task.issue), None)
    # Las tareas "skill:<id>" las crea skill_service (no son del catálogo).
    title = issue.fix_title if issue else task.issue
    if not issue and task.issue.startswith("skill:"):
        title = f"Skill {task.issue.removeprefix('skill:')}"
    return {
        "id": task.id,
        "vmId": task.vm_id,
        "issue": task.issue,
        "title": title,
        "status": task.status,
        "log": task.log,
        # Skill de Claude con sesión: terminó su turno y el usuario puede seguir la conversación.
        "canReply": task.issue.startswith("skill:") and bool(task.session_id) and task.status != "running",
        "createdAt": _iso(task.created_at),
        "finishedAt": _iso(task.finished_at),
    }


async def _connect(vm: TestVm) -> asyncssh.SSHClientConnection:
    try:
        conn = await asyncssh.connect(**vm_service.connect_options(vm))
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        raise ServiceError(vm_service.connection_error(vm, exc), 502) from exc
    async with AsyncSessionLocal() as db:
        stored = await db.get(TestVm, vm.id)
        if vm_service.remember_host_key(stored, conn):
            await db.commit()
    return conn


async def diagnose(db: AsyncSession, actor: Actor, vm_id: int, group: str = "doctor", base_url: str = "") -> dict:
    """Corre los chequeos del grupo en una sola conexión. Rápido: es sincrónico."""
    if group not in GROUPS:
        raise NotFoundError("Grupo de chequeos desconocido")
    vm = await vm_service.get_vm(db, actor, vm_id)
    issues = [i for i in _issues() if i.group == group]
    env = await _routine_env(db, actor, vm, base_url, new_token=False) if group == "routine" else {}
    lines = [_ROOT, _vm_env(vm), *_env_lines(env)] + [
        f"if ( {i.check} ) >/dev/null 2>&1; then echo 'ODV {i.id} ok'; else echo 'ODV {i.id} fail'; fi"
        for i in issues
    ]
    async with await _connect(vm) as conn:
        try:
            result = await asyncio.wait_for(conn.run(f"sh -c {shlex.quote(chr(10).join(lines))}", check=False),
                                            DIAGNOSE_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise ServiceError("El diagnóstico tardó demasiado", 504) from exc
    found = dict(re.findall(r"^ODV (\S+) (ok|fail)$", str(result.stdout or ""), re.M))
    return {
        "checks": [_issue_dict(i, found.get(i.id) == "ok", vm) for i in issues if i.id in found],
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }


_tasks: set[asyncio.Task] = set()


async def _routine_env(db: AsyncSession, actor: Actor, vm: TestVm, base_url: str, new_token: bool) -> dict:
    """Variables de los comandos del grupo `routine`: URL de optidev y prefijo
    del token MCP vigente de la rutina de esta VM. Con `new_token` crea uno
    (en $ODV_TOKEN) y revoca el anterior (sin commit)."""
    url = (settings.PUBLIC_URL or base_url).rstrip("/")
    if not url:
        raise ServiceError("No se sabe la URL de optidev para la VM: configurá PUBLIC_URL")
    name = ROUTINE_TOKEN_NAME.format(vm=vm.name)[:100]
    current = (await db.execute(select(ApiToken).where(
        ApiToken.user_id == actor.user.id, ApiToken.name == name, ApiToken.revoked_at.is_(None)))).scalars().all()
    if not new_token:
        return {"ODV_URL": url, "ODV_PREFIX": current[-1].prefix if current else ""}
    for token in current:
        token.revoked_at = datetime.now(timezone.utc)
    raw = generate_api_token()
    db.add(ApiToken(user_id=actor.user.id, name=name, token_hash=hash_api_token(raw), prefix=raw[:10]))
    return {"ODV_URL": url, "ODV_PREFIX": raw[:10], "ODV_TOKEN": raw}


def _env_lines(env: dict[str, str]) -> list[str]:
    return [f"{key}={shlex.quote(value)}" for key, value in env.items()]


async def start_fix(db: AsyncSession, actor: Actor, vm_id: int, issue_id: str, base_url: str = "") -> dict:
    vm = await vm_service.get_vm(db, actor, vm_id)
    vm_service.ensure_not_self(vm)
    issue = _issue(issue_id)
    if not issue.fix:
        raise ServiceError("Este problema no tiene arreglo automático")
    running = (await db.execute(select(VmTask.id).where(VmTask.vm_id == vm.id, VmTask.status == "running"))).first()
    if running:
        raise ConflictError(f"Ya se está arreglando algo en {vm.name}; esperá a que termine")
    env = await _routine_env(db, actor, vm, base_url, issue.mcp_token) if issue.group == "routine" else {}
    task = VmTask(user_id=actor.user.id, vm_id=vm.id, issue=issue.id, status="running", log="")
    db.add(task)
    await db.commit()
    job = asyncio.create_task(_run_fix(task.id, vm.id, issue, env))
    _tasks.add(job)
    job.add_done_callback(_tasks.discard)
    return task_dict(task)


async def get_task(db: AsyncSession, actor: Actor, task_id: int) -> dict:
    task = await db.get(VmTask, task_id)
    if not task or task.user_id != actor.user.id:
        raise NotFoundError("Tarea no encontrada")
    return task_dict(task)


async def fail_interrupted() -> None:
    async with AsyncSessionLocal() as db:
        for task in (await db.execute(select(VmTask).where(VmTask.status == "running"))).scalars():
            task.status = "failed"
            task.log += "\n[optidev se reinició durante el arreglo; volvé a intentarlo]\n"
            task.finished_at = datetime.now(timezone.utc)
        await db.commit()


async def _run_fix(task_id: int, vm_id: int, issue: Issue, env: dict[str, str] | None = None) -> None:
    async with AsyncSessionLocal() as db:
        vm = await db.get(TestVm, vm_id)
        password = vm_service.connect_options(vm).get("password")
    env = env or {}
    log = JobLog(VmTask, task_id, [password or "", env.get("ODV_TOKEN", "")])
    done = {"force": True, "finished_at": None}
    try:
        await log.write(f"▶ {issue.fix_title} en {vm.name} ({vm.username}@{vm.host})\n")
        async with await _connect(vm) as conn:
            script = chr(10).join([_ROOT, _vm_env(vm), *_env_lines(env), 'set -e', issue.fix])
            code = await run_remote(log, conn, f"sh -c {shlex.quote(script)}", FIX_TIMEOUT)
            if code != 0:
                raise ServiceError(f"El arreglo terminó con código {code}")
            await log.write("\n▶ Verificando\n")
            check_script = chr(10).join([_ROOT, _vm_env(vm), *_env_lines(env), issue.check])
            check = await run_remote(log, conn, f"sh -c {shlex.quote(check_script)} >/dev/null 2>&1",
                                     DIAGNOSE_TIMEOUT)
            if check != 0:
                raise ServiceError("El arreglo terminó pero el problema sigue")
        done["finished_at"] = datetime.now(timezone.utc)
        await log.write(f"\n✔ {issue.fix_title}: listo\n", status="success", **done)
    except ServiceError as exc:
        done["finished_at"] = datetime.now(timezone.utc)
        await log.write(f"\n✖ {exc.message}\n", status="failed", **done)
    except Exception as exc:  # noqa: BLE001 — cualquier otra cosa también tiene que cerrar la tarea
        logger.exception("Arreglo %s falló", task_id)
        done["finished_at"] = datetime.now(timezone.utc)
        await log.write(f"\n✖ Error inesperado: {exc}\n", status="failed", **done)
