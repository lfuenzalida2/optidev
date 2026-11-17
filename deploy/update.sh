#!/usr/bin/env bash
# shellcheck disable=SC2016  # las comillas simples en `bash -c` son a propósito
# Actualiza optidev a la última versión de `master`: fast-forward, dependencias
# del backend, respaldo de la base + migraciones, build del frontend,
# configuración del servidor (clave de las VMs en el .env y sitio de nginx),
# reinicio del backend y chequeo de salud. Si algo falla vuelve a la versión
# anterior.
#
# Ojo: corre la versión de este script que había ANTES del pull (bash ya lo
# leyó entero). Un cambio en el script se aplica desde la actualización
# siguiente a la que lo trae.
#
# Lo corre la unidad `optidev-update.service` (root), que lanza
# `optidev-update.path` cuando la app crea el archivo de pedido
# ($REQUEST_DIR/request). Ver README.md ("Actualización desde la app").
#
# Modelo de seguridad:
#   - Hoy el backend corre como root desde /root/work/optidev, y por eso
#     APP_USER es root por defecto. El script soporta un usuario sin privilegios
#     (APP_USER=optidev, ver README.md) para cuando se quiera separar.
#   - El backend lo único que hace es crear el archivo de pedido. Este script
#     ignora su contenido: no recibe rama, commit ni comandos desde la app.
#   - Como root solo se ejecutan git (sin hooks) sobre el repo, systemctl y la
#     copia del build al web root. Todo lo que ejecuta código del repo o de
#     terceros (pip, alembic, yarn) corre como $APP_USER.
#   - Antes de hacer nada se valida que el script, el repo y el directorio de
#     estado sean de root y no los pueda modificar otro usuario (en toda la
#     ruta hasta `/`); si no, se aborta.
#   - Estado, log y lock viven en $STATE_DIR (de root, legible por todos), no
#     en el directorio de datos del backend: así $APP_USER no puede poner ahí
#     un symlink para que root escriba en otro archivo.
#
# Configuración (variables de entorno, con defaults para el layout de
# README.md, "Actualización desde la app"). No hay puertos fijos: el del backend sale de
# HEALTH_URL, BACKEND_PORT o del `.env` del backend, en ese orden.

set -uo pipefail
umask 022

# Todo el script va entre llaves: bash lo parsea completo antes de ejecutarlo,
# así el `git merge`/`git reset` que reemplaza este mismo archivo no afecta a
# la corrida en curso.
{

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
APP_DIR="${APP_DIR:-$(dirname "$(dirname "$SELF")")}"
BRANCH="master"   # fijo a propósito: no se acepta desde afuera
APP_USER="${APP_USER:-root}"
SERVICE="${SERVICE:-optidev-backend}"
WEB_ROOT="${WEB_ROOT:-/var/www/optidev}"
STATE_DIR="${STATE_DIR:-/var/lib/optidev-update}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"
# Sitio de nginx que se mantiene igual a deploy/nginx/optidev.conf. Vacío =
# no tocar nginx (p. ej. si el proxy lo maneja otra cosa).
NGINX_SITE="${NGINX_SITE-/etc/nginx/sites-available/optidev}"
NGINX_BIN="${NGINX_BIN:-nginx}"  # no `NGINX`: nginx lee esa variable de entorno (sockets heredados)

BACKEND="$APP_DIR/services/backend"
FRONTEND_SRC="services/frontend"
DATA_DIR="${DATA_DIR:-$BACKEND/data}"
REQUEST_DIR="${REQUEST_DIR:-$DATA_DIR/update}"
DB_FILE="${DB_FILE:-$DATA_DIR/optidev.db}"
BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
BUILD_DIR="${BUILD_DIR:-$DATA_DIR/frontend-build}"
VENV="${VENV:-$BACKEND/.venv}"

ENV_FILE="${ENV_FILE:-$BACKEND/.env}"
NGINX_SRC="$APP_DIR/deploy/nginx/optidev.conf"
NGINX_PREV="$STATE_DIR/nginx-site.prev"

LOG="$STATE_DIR/update.log"
STATUS="$STATE_DIR/status.json"

if [ -z "${HEALTH_URL:-}" ]; then
  port="${BACKEND_PORT:-}"
  if [ -z "$port" ] && [ -f "$BACKEND/.env" ]; then
    # Solo se lee esta línea: el .env tiene secretos y no se carga entero.
    port="$(sed -n 's/^[[:space:]]*BACKEND_PORT[[:space:]]*=[[:space:]]*["'\'']\{0,1\}\([0-9]\{1,5\}\).*/\1/p' "$BACKEND/.env" | tail -n 1)"
  fi
  HEALTH_URL="http://127.0.0.1:${port:-8000}/api/health"
fi

die_early() { echo "optidev-update: $*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die_early "tiene que correr como root"

# --- Validación de rutas --------------------------------------------------

# Falla si `path` o algún directorio hasta `/` no es de root o lo puede
# escribir otro usuario (salvo directorios con sticky bit, como /tmp, donde
# nadie más puede renombrar entradas de root).
assert_root_chain() {
  local p
  p="$(readlink -f "$1")" || return 1
  [ -e "$p" ] || { echo "no existe: $1"; return 1; }
  while :; do
    local owner mode
    owner="$(stat -c %u "$p")" && mode="$(stat -c %a "$p")" || return 1
    if [ "$owner" != 0 ]; then echo "$p no es de root"; return 1; fi
    local perm=$((8#$mode))
    if [ -d "$p" ] && [ $((perm & 01000)) -ne 0 ]; then
      :  # sticky: otros usuarios no pueden reemplazar lo que es de root
    elif [ $((perm & 0022)) -ne 0 ]; then
      echo "$p lo pueden escribir otros usuarios (modo $mode)"; return 1
    fi
    [ "$p" = / ] && return 0
    p="$(dirname "$p")"
  done
}

mkdir -p "$STATE_DIR"
chmod 755 "$STATE_DIR"
msg="$(assert_root_chain "$STATE_DIR")" || die_early "ruta insegura, no se actualiza: $msg"

# --- Lock y consumo del pedido ---------------------------------------------

exec 9>"$STATE_DIR/lock"
# Bajo systemd no puede haber dos corridas (la unidad no arranca de nuevo si
# está activa); el lock cubre corridas a mano. Se espera en vez de salir para
# no dejar el pedido sin consumir.
flock -w 1800 9 || die_early "no se pudo tomar el lock"

# Se borra el pedido antes que nada: si quedara, la .path relanzaría la unidad
# en bucle. Un pedido creado mientras esto corre dispara una corrida más al
# terminar (que normalmente termina en "ya estaba al día").
if [ -d "$REQUEST_DIR" ] && [ ! -L "$REQUEST_DIR" ]; then
  rm -f -- "$REQUEST_DIR/request"
fi

[ -f "$LOG" ] && mv -f "$LOG" "$LOG.1"
: >"$LOG"
chmod 644 "$LOG"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Helpers ----------------------------------------------------------------

# Saca credenciales de URLs (https://usuario:token@host) antes de escribir al log.
redact() { sed -u -E 's#(://)[^/@[:space:]]+@#\1***@#g'; }
log() { echo "[$(date -u +%H:%M:%S)] $*" | redact >>"$LOG"; }
run() { log "\$ $*"; "$@" 2>&1 | redact >>"$LOG"; return "${PIPESTATUS[0]}"; }

# Como $APP_USER, con su HOME. Desde $1 (directorio de trabajo).
as_app() {
  local dir="$1"; shift
  local home
  home="$(getent passwd "$APP_USER" | cut -d: -f6)"
  log "\$ ($APP_USER en $dir) $*"
  setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups --reset-env -- \
    env HOME="$home" PATH="$PATH" bash -c 'cd "$1" && shift && exec "$@"' _ "$dir" "$@" 2>&1 | redact >>"$LOG"
  return "${PIPESTATUS[0]}"
}

git_() { git -C "$APP_DIR" -c core.hooksPath=/dev/null "$@"; }

# status <state> <step> <message> — los textos son fijos de este script.
status() {
  python3 -I - "$STATUS" "$started_at" "$1" "$2" "$3" "${prev:-}" "${new:-}" <<'EOF'
import datetime, json, os, sys
path, started, state, step, message, prev, new = sys.argv[1:8]
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump({
        "state": state, "step": step, "message": message,
        "fromCommit": prev or None, "toCommit": new or None, "startedAt": started,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, f)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
EOF
}

finish() { status "$1" "$2" "$3"; log "$3"; exit "${4:-0}"; }

backend_healthy() {
  local _
  for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
    curl -fsS -m 5 "$HEALTH_URL" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

prev=""
new=""
migrated=0
published=0
nginx_changed=0
backup=""
current_step="init"

# Si el script muere por algo no previsto (no por un `exit` controlado), que
# el estado no quede en "running" para siempre.
# shellcheck disable=SC2329  # se invoca vía trap
on_exit() {
  local code=$?
  if [ "$code" != 0 ] && grep -q '"state": "running"' "$STATUS" 2>/dev/null; then
    status failed "$current_step" "La actualización se cortó inesperadamente; revisar el servidor."
  fi
}
trap on_exit EXIT

rollback() {
  local step="$1"
  trap - EXIT
  log "Falló en '$step': volviendo a $prev"
  status running rollback "Falló en '$step', volviendo a la versión anterior"
  local ok=1
  run git_ reset --hard "$prev" || ok=0
  as_app "$BACKEND" "$VENV/bin/pip" install -q -r requirements.txt || ok=0
  if [ "$migrated" = 1 ]; then
    log "Restaurando la base desde $backup"
    run "$SYSTEMCTL" stop "$SERVICE"
    as_app "$DATA_DIR" python3 -I -c '
import os, shutil, sys
db, bak = sys.argv[1], sys.argv[2]
for suffix in ("-wal", "-shm"):
    if os.path.exists(db + suffix):
        os.remove(db + suffix)
shutil.copyfile(bak, db + ".restore")
os.replace(db + ".restore", db)
' "$DB_FILE" "$backup" || ok=0
  fi
  if [ "$published" = 1 ] && [ -d "$WEB_ROOT.prev" ]; then
    { rm -rf "$WEB_ROOT" && mv "$WEB_ROOT.prev" "$WEB_ROOT"; } || ok=0
  fi
  if [ "$nginx_changed" = 1 ]; then
    log "Restaurando la configuración de nginx anterior"
    { run cp -p "$NGINX_PREV" "$NGINX_SITE" && run "$NGINX_BIN" -t && run "$SYSTEMCTL" reload nginx; } || ok=0
  fi
  run "$SYSTEMCTL" restart "$SERVICE" || ok=0
  if [ "$ok" = 1 ] && backend_healthy; then
    finish failed "$step" "Falló en '$step'. Se volvió a la versión anterior; ver el log." 1
  fi
  finish failed "$step" "Falló en '$step' y la vuelta atrás no terminó bien: revisar el servidor." 1
}

# --- 0. Validaciones (después de consumir el pedido, para que la falla quede
#        en el estado que ve la app y la .path no relance en bucle) ----------

for path in "$SELF" "$APP_DIR" "$APP_DIR/.git" "$(dirname "$WEB_ROOT")"; do
  if ! msg="$(assert_root_chain "$path")"; then
    log "Ruta insegura: $msg"
    finish failed init "Ruta insegura en el servidor, no se actualiza; ver el log." 1
  fi
done
id "$APP_USER" >/dev/null 2>&1 || finish failed init "No existe el usuario $APP_USER." 1

# --- 1. Traer cambios: solo fast-forward de master, sin cambios locales -----

current_step="fetch"
prev="$(git_ rev-parse HEAD)" || finish failed fetch "No se pudo leer el commit actual del repo." 1
status running fetch "Buscando cambios"
log "Repo $APP_DIR en $prev"

[ "$(git_ rev-parse --abbrev-ref HEAD)" = "$BRANCH" ] ||
  finish failed fetch "El servidor no está en la rama $BRANCH; no se actualiza." 1
if [ -n "$(git_ status --porcelain --untracked-files=no)" ]; then
  run git_ status --short
  finish failed fetch "Hay cambios locales en el servidor; no se actualiza." 1
fi
if [ "$(stat -c %U "$VENV")" != "$APP_USER" ]; then
  finish failed fetch "El virtualenv $VENV tiene que ser de $APP_USER (ver README.md)." 1
fi
run git_ fetch --no-tags origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" ||
  finish failed fetch "No se pudo hacer git fetch; ver el log." 1
new="$(git_ rev-parse "origin/$BRANCH")"
if [ "$prev" = "$new" ]; then
  finish success none "Ya estaba al día."
fi
run git_ log --oneline --no-decorate "$prev..$new"
current_step="pull"
run git_ merge --ff-only "origin/$BRANCH" ||
  finish failed pull "master no avanza en fast-forward (¿commits locales?); no se actualiza." 1

# --- 2. Dependencias del backend --------------------------------------------

current_step="dependencies"
status running dependencies "Instalando dependencias del backend"
as_app "$BACKEND" "$VENV/bin/pip" install -q -r requirements.txt || rollback dependencies

# --- 3. Respaldo de la base y migraciones -----------------------------------
# El respaldo usa la API de backup de SQLite (consistente aunque el backend
# esté escribiendo). Lo que se escriba entre el respaldo y el final de la
# actualización se pierde si hay que restaurarlo.

current_step="migrations"
status running migrations "Respaldando la base y corriendo migraciones"
backup="$BACKUP_DIR/optidev-$(date -u +%Y%m%dT%H%M%SZ)-${prev:0:12}.db"
[ -f "$DB_FILE" ] || { log "No existe la base $DB_FILE"; rollback backup; }
as_app "$DATA_DIR" python3 -I -c '
import os, sqlite3, sys
db, dst = sys.argv[1], sys.argv[2]
os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
src, out = sqlite3.connect(db), sqlite3.connect(dst + ".tmp")
src.backup(out)
out.close(); src.close()
os.replace(dst + ".tmp", dst)
' "$DB_FILE" "$backup" || rollback backup
migrated=1
as_app "$BACKEND" "$VENV/bin/alembic" upgrade head || rollback migrations
# Quedan los últimos $KEEP_BACKUPS respaldos.
as_app "$BACKUP_DIR" bash -c 'ls -1t optidev-*.db 2>/dev/null | tail -n +$(($1 + 1)) | xargs -r rm -f --' _ "$KEEP_BACKUPS"

# --- 4. Frontend --------------------------------------------------------------
# Se construye como $APP_USER en $BUILD_DIR (el repo es de root). node_modules
# se conserva entre corridas; el resto se reemplaza por el código del commit.

current_step="frontend"
status running frontend "Construyendo el frontend"
as_app "$DATA_DIR" bash -c 'mkdir -p "$1" && find "$1" -mindepth 1 -maxdepth 1 ! -name node_modules -exec rm -rf -- {} +' _ "$BUILD_DIR" ||
  rollback frontend
log "\$ git archive HEAD $FRONTEND_SRC | tar -x -C $BUILD_DIR (como $APP_USER)"
git_ archive --format=tar HEAD "$FRONTEND_SRC" |
  setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups -- \
    tar -x -C "$BUILD_DIR" --strip-components=2 2>>"$LOG" || rollback frontend
as_app "$BUILD_DIR" yarn install --frozen-lockfile --non-interactive --silent || rollback frontend
as_app "$BUILD_DIR" yarn build || rollback frontend

# Publicación: se lee dist como $APP_USER (así no puede colar archivos que
# solo root puede leer) y root lo extrae en un directorio nuevo, sin symlinks,
# y lo intercambia con el web root.
rm -rf -- "$WEB_ROOT.new" "$WEB_ROOT.prev"
mkdir -m 755 "$WEB_ROOT.new" || rollback frontend
log "\$ publicar $BUILD_DIR/dist en $WEB_ROOT"
setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups -- tar -C "$BUILD_DIR/dist" -cf - . |
  tar -C "$WEB_ROOT.new" -xf - --no-same-owner --no-same-permissions 2>>"$LOG" || rollback frontend
find "$WEB_ROOT.new" -type l -delete
chmod -R u=rwX,go=rX "$WEB_ROOT.new"
[ -f "$WEB_ROOT.new/index.html" ] || rollback frontend
if [ -d "$WEB_ROOT" ]; then
  mv "$WEB_ROOT" "$WEB_ROOT.prev" || rollback frontend
fi
published=1
mv "$WEB_ROOT.new" "$WEB_ROOT" || rollback frontend

# --- 5. Configuración del servidor -------------------------------------------
# Lo que antes había que hacer a mano al desplegar, para que pasar de staging a
# master sea solo apretar "Actualizar".

# 5a. Clave de cifrado de las credenciales de las VMs de pruebas. Solo se
# genera si no hay ninguna credencial guardada: las que existan están cifradas
# con la clave derivada de JWT_SECRET y una clave nueva las dejaría ilegibles.
current_step="config"
if [ -f "$ENV_FILE" ] && ! grep -qE '^[[:space:]]*VM_CREDENTIALS_KEY[[:space:]]*=[[:space:]]*[^[:space:]#]' "$ENV_FILE"; then
  saved="$(python3 -I -c '
import sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    print(con.execute("SELECT COUNT(*) FROM test_vms WHERE deleted_at IS NULL").fetchone()[0])
except sqlite3.OperationalError:
    print(0)  # todavía no existe la tabla
' "$DB_FILE")" || rollback config
  if [ "$saved" = 0 ]; then
    # El valor no pasa por el log ni por la línea de comandos.
    python3 -I -c '
import secrets, sys
path = sys.argv[1]
with open(path, "rb") as f:
    text = f.read()
with open(path, "ab") as f:
    if text and not text.endswith(b"\n"):
        f.write(b"\n")
    f.write(b"VM_CREDENTIALS_KEY=" + secrets.token_hex(32).encode() + b"\n")
' "$ENV_FILE" || rollback config
    log "Se generó VM_CREDENTIALS_KEY en $ENV_FILE"
  else
    log "No se genera VM_CREDENTIALS_KEY: hay $saved credenciales cifradas con la clave derivada de JWT_SECRET"
  fi
fi

# 5b. nginx: el sitio queda igual a deploy/nginx/optidev.conf. Se valida con
# `nginx -t` antes de recargar; si falla, vuelve la configuración anterior.
current_step="nginx"
if [ -n "$NGINX_SITE" ] && ! cmp -s "$NGINX_SRC" "$NGINX_SITE"; then
  status running nginx "Actualizando la configuración de nginx"
  if [ -f "$NGINX_SITE" ]; then
    cp -p "$NGINX_SITE" "$NGINX_PREV" || rollback nginx
    nginx_changed=1
  fi
  run install -m 644 "$NGINX_SRC" "$NGINX_SITE" || rollback nginx
  run "$NGINX_BIN" -t || rollback nginx
  run "$SYSTEMCTL" reload nginx || rollback nginx
fi

# --- 6. Reinicio y chequeo de salud ------------------------------------------

current_step="restart"
status running restart "Reiniciando el backend"
run "$SYSTEMCTL" restart "$SERVICE" || rollback restart
backend_healthy || rollback restart
rm -rf -- "$WEB_ROOT.prev"

trap - EXIT
finish success "done" "Actualizado de ${prev:0:7} a ${new:0:7}."
# shellcheck disable=SC2317  # red de seguridad: `finish` ya sale
exit 0
}
