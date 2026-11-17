# Optidev

Tablero kanban interno de Optidev para organizar el trabajo de desarrollo y
delegarlo a Claude Code. Reemplaza al tablero de Trello: cada tarjeta indica en
qué **repositorios** hay que trabajar, y Claude lee y mueve las tarjetas a
través de un **servidor MCP** integrado en el backend.

```
services/
  backend/    FastAPI + SQLAlchemy async + MySQL 8 (alembic; SQLite en tests/CI) + servidor MCP en /mcp
  frontend/   React 19 + Vite + antd + dnd-kit
scripts/
  import_trello.py   importa un export JSON de Trello vía la API
```

## Conceptos

| Concepto | Qué es |
|---|---|
| **Lista** | Estado de la tarea. Por defecto: Pendiente, En proceso, PR, Testing, Hecho. Una lista puede marcarse como *ocultable* (menú de la lista → "Permitir ocultar", p. ej. Backlog): en la web se ve colapsada por defecto y cada usuario puede expandirla (se recuerda en su navegador). Es solo visual: la API y el MCP siguen devolviendo sus tarjetas. |
| **Tarjeta** | Una tarea: título, descripción (markdown), responsable, repos, links de PR, comentarios e historial. Se archiva, no se borra. |
| **Repositorio** | Repo de la organización con una `key` corta (`helios`, `mms-backend`…), URL de GitHub, rama base y ruta local donde está clonado en la máquina de Claude. Se cargan a mano desde la app. |
| **Token personal** (`odv_…`) | Credencial para el MCP. Lo que hace Claude queda a nombre del dueño del token, marcado como "Claude" (`via=mcp`). |

## Desarrollo local

Requisitos: Python 3.11, Node 22, yarn.

```bash
cp .env.example .env            # completar JWT_SECRET (openssl rand -hex 32) y ADMIN_PASSWORD

# Backend (puerto 8000)
cd services/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp ../../.env .env
.venv/bin/alembic upgrade head
.venv/bin/python main.py

# Frontend (puerto 5173 o VITE_DEV_PORT, hace proxy de /api y /mcp al backend)
cd services/frontend
yarn install && yarn dev
```

Tests: `cd services/backend && .venv/bin/pytest`.

Al arrancar con la base vacía se crean el usuario admin (`ADMIN_USERNAME` /
`ADMIN_PASSWORD`) y las listas por defecto.

## Despliegue

```bash
cp .env.example .env   # JWT_SECRET, ADMIN_PASSWORD, DB_*, FRONTEND_PORT
docker compose up -d --build
```

- `frontend` (nginx) publica `FRONTEND_PORT` (por defecto **80**, HTTP) y hace
  proxy de `/api` y `/mcp` al backend: la app y el MCP quedan bajo el mismo
  origen. nginx escucha en el 80 dentro del contenedor; `FRONTEND_PORT` solo
  cambia el puerto del host (usar otro si el 80 ya está ocupado, p. ej. por un
  proxy en la misma máquina). Poner HTTPS delante
  (Traefik/Fortinet): los tokens viajan en el header `Authorization`.
- La base (MySQL 8) **no** es parte de este compose: corre aparte, se mantiene
  arriba por su cuenta y el backend se conecta con las `DB_*` de `.env`
  (`DB_DIALECT`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`);
  sin `DB_HOST` `docker compose up` falla. Respaldarla donde esté (p. ej.
  `mysqldump`). Las imágenes subidas siguen en el volumen `optidev-data`
  (`data/uploads`, `UPLOAD_DIR`).
- El backend corre **un solo proceso**: el session manager del MCP vive en
  memoria. No escalar a varias réplicas sin externalizarlo.
- Los únicos "solo entre no borrados" (usuarios, repos) usan un
  índice parcial en SQLite y un índice sobre expresión en MySQL
  (`active_unique_index` en `src/config/database.py`). Nuevos únicos con soft
  delete: usar ese helper.
- `deploy/update.sh` (respaldo/restauración) y el comando de MFA de abajo
  todavía asumen SQLite; adaptarlos antes de usarlos con MySQL.
- Las migraciones (`alembic upgrade head`) corren solas al arrancar el backend.
- **Dashboard de PRs** (menú *PRs*): muestra los PRs abiertos de todos los
  repositorios cargados (ramas, autor, etiquetas, antigüedad y estado de GitHub
  Actions). Requiere `GITHUB_TOKEN` en el `.env` del backend con lectura de esos
  repos; no guarda nada en la base: lee de un caché en memoria que el backend
  renueva solo cada `GITHUB_PULLS_REFRESH_SECONDS` (300 s por defecto), con el
  botón *Actualizar* o al aprobar/mergear un PR desde optidev.
- **Salud de repos** (*Configuración → Repositorios*: check verde o cruz roja por repo y, al abrirlo, qué está bien y qué mal): revisa que todos los repositorios cargados
  sigan el mismo estándar, con `GITHUB_TOKEN`: (1) `master` (`REPO_HEALTH_BRANCHES`)
  sin push directo (protección de rama con PR obligatorio o push restringido,
  o un ruleset con `pull_request`/`update`), (2) existe la rama `staging`, de
  donde salen siempre los PR, (3) exactamente los mismos labels con los mismos
  colores, ni uno más ni uno menos: los del repo de referencia
  `REPO_HEALTH_REFERENCE_REPO` (la key de un repo del tablero, por defecto
  `mms-backend`) más los del flujo de PRs que ese repo no tenga (*Aprobado*,
  *Revisado*, *Pendiente*, *Waiting* y *Breaking Change*, con los colores de
  mms-backend; `FLOW_LABELS`); si se deja vacío, solo esos cinco y no se
  penalizan los de más), (4) repo privado y (5) como mínimo los mismos
  workflows de GitHub Actions, por nombre y activos (*Build and Push Docker
  Images*, *Copilot code review*, *Dependency graph* y *Promote to
  Production*; a dónde apunta cada uno cambia); los workflows de más se
  muestran como información, no como error. Además, dos chequeos
  *informativos* del estándar de agentes: (6) `CLAUDE.md` en la raíz, con un
  enlace a `repo-docs/<repo>` (la documentación del repo de agentes) y una
  sección «Verificar», y (7) `.claude/settings.json` con el marketplace
  `optidev-tools` y los plugins de `CLAUDE_PLUGINS`; (8) la documentación:
  existe `repo-docs/<repo>/README.md` en el repo de agentes (`WORKFLOW_REPO_KEY`)
  y el commit de su `documentado_en_commit` está a lo más a
  `DOCS_MAX_COMMITS_BEHIND` (50) commits de su `documentado_en_rama`; (9) algún
  workflow corre tests y lint (se detecta por comandos comunes, es una
  heurística); (10) `staging` también sin push directo; y (11), solo en el repo
  de agentes, `master` exige status checks y existe el workflow
  `Validate marketplace`. El de CI (9) se arregla abriendo un PR contra `staging` con un
  `.github/workflows/ci.yml` que corre los tests y el lint que faltan, deducidos de la raíz del repo
  (Node con yarn/npm, Python con pytest/ruff); si ya hay un `ci.yml` o no se puede deducir (monorepo,
  pnpm), queda a mano. Documentación, `staging` protegido (a propósito: la skill `pr-review-merge`
  hace push directo a `staging`) y CI del marketplace se arreglan a mano por ahora. Un
  arreglo pedido sobre un chequeo que no aplica al repo se rechaza. El estándar vigente se ve en
  el apartado *Estándar por defecto de los repositorios*, arriba de la lista.
  Cada chequeo da OK, Falla, «Sin verificar» (no se pudo leer; no cuenta como
  falla) o «No aplica». Cada uno tiene una severidad en `CHECK_SPECS`
  (`repo_health_service.py`): *exigido* (si falla, el repo no cumple) o
  *informativo* (se muestra con un aviso naranja, pero no cuenta como falla ni
  lo aplica *Arreglar todo*; un chequeo nuevo parte así y pasa a exigido al
  terminar su despliegue). Un chequeo también puede limitarse a ciertos repos
  (`repos`: sus keys); en los demás queda «No aplica» y no se consulta a GitHub.
  Arriba de la lista, *Avance del estándar* muestra cuántos repos cumplen cada
  chequeo y filtra la lista a los que faltan. Al cargar un repo (o cambiarle la URL de GitHub) el backend lo
  revisa en el momento, sin esperar a la revisión diaria, y si algún chequeo exigido falla o no se pudo
  verificar avisa a los admins en la campana (los informativos no avisan). Como `GITHUB_TOKEN` suele no
  tener permisos de administración, la protección de ramas de un repo nuevo queda «Sin verificar» hasta que un
  admin lo revise con su token. Leer las protecciones necesita un token con permisos de
  administración del repo. La revisión queda en caché en memoria: la primera
  vista la hace, el backend la renueva solo cada `REPO_HEALTH_INTERVAL_SECONDS`
  (86400 s, no revisa al arrancar) y deja en el log lo que falla; *Revisar
  estándar* la fuerza. La revisión solo informa; corregir es aparte: cada
  chequeo que falla tiene un botón *Arreglar* (y el repo, *Arreglar todo*, solo
  admins) que pide **un token de GitHub temporal con permisos de
  administración** del repo: se usa solo en esa petición (no se guarda, no se
  loguea ni vuelve en la respuesta; el navegador lo recuerda en memoria hasta
  recargar) y también para volver a revisar el repo. Los arreglos son
  idempotentes: protegen `master` (PR obligatorio sin exigir aprobaciones,
  también para admins, sin force push ni borrado; si ya hay una protección solo
  le agrega el PR obligatorio), crean `staging` desde `master`, dejan los
  labels como el estándar (crean los que faltan, corrigen colores y **borran
  los que sobran**, que también se quitan de las issues y PRs que los tengan),
  pasan el repo a privado y habilitan Actions y los workflows estándar
  deshabilitados. Los workflows que faltan no se copian: hay que
  agregarlos a mano.
- **Workflow** (menú *Workflow*): el repositorio de agentes (el del tablero con
  key `WORKFLOW_REPO_KEY`, `agents` por defecto) navegable como en GitHub, más
  un chat con Claude (`ANTHROPIC_API_KEY`, modelo `ANTHROPIC_MODEL`, Sonnet por
  defecto) que lee sus archivos y puede proponer cambios. Una propuesta no se
  aplica sola: se ve con su diff y, si alguien la aprueba, el backend crea una
  rama `workflow/…`, el commit y un PR contra la rama base del repo (queda a
  nombre de quien aprueba, con su token personal de GitHub, que necesita
  escritura de Contents y Pull requests en ese repo). No se pueden tocar archivos de `.github/` (un
  workflow nuevo correría con los secrets del repo). El chat no guarda nada en
  el servidor: la conversación vive en el navegador de cada usuario. Un mensaje
  puede tardar más de un minuto; nginx tiene un `location` propio para
  `/api/workflow/chat` con `proxy_read_timeout` largo.
- **VM de pruebas** (*Mi perfil*): cada usuario carga la IP y las credenciales
  SSH (contraseña o clave privada) de su VM. Se guardan cifradas con
  `VM_CREDENTIALS_KEY` (o una clave derivada de `JWT_SECRET`) y la API nunca
  las devuelve. La primera conexión exitosa fija la clave del host; si cambia,
  optidev no se conecta hasta que el usuario la olvide al editar la VM.
  Un usuario sin VM puede **Solicitar VM** (una solicitud abierta por usuario):
  se avisa a los admins en la campana del header. Los medios de aviso se
  activan o desactivan en *Configuración → Notificaciones* (hoy solo la
  campana; desactivarla no impide el pedido, solo el aviso).
  Los admins tienen la vista **VMs**: ven las de todos los usuarios, abren la
  terminal, prueban, editan y eliminan cualquiera, y crean la VM de quien la
  solicitó (se cierra la solicitud y se le avisa). El diagnóstico, las skills y
  las previews siguen siendo solo del dueño de la VM: instalan en ella un token
  a nombre de quien los corre.
- **Chat de Claude** (botón abajo a la derecha, sobre el de las terminales): una
  terminal flotante que corre Claude Code (`claude`) por SSH en una VM del usuario,
  con un contexto fijo del tablero (usa el MCP de optidev y las skills de
  Optidev para consultar tarjetas y resolver PRs). El usuario elige la VM y qué
  puede hacer Claude sin preguntar (`--permission-mode`: pregunta siempre, acepta
  ediciones o solo planifica; cambiarlo reinicia la sesión). Minimizado, la
  sesión sigue viva. La VM necesita Claude Code, las skills y el MCP (ver el
  diagnóstico de la VM). No hay backend propio: reutiliza la terminal SSH.
- **Aprobar / Rechazar QA** (vista *Testing*): *Aprobar* pone el label
  **Aprobado** en todos los PRs abiertos de la tarjeta (crea el label en el
  repo si no existe) y lo registra en el historial; **no mueve la tarjeta**:
  sigue siendo un PR hasta que *Mergear PR* la pasa a Hecho. Funciona desde
  cualquier lista y falla si la tarjeta no tiene PRs abiertos. Es la misma
  convención que `scripts/rebase-approved-prs.sh` de Helios. Un PR queda
  *Aprobado* solo si además pasó la revisión de código (skill `revisar-pr` de
  optidev-agents) sin pendientes: si algún PR tiene **Revisado** o
  **Waiting**, la aprobación se rechaza sin etiquetar nada; al aprobar se le
  saca **Pendiente**. *Rechazar* (pide notas) devuelve los PRs a **Revisado**
  (les saca **Aprobado** y les pone **Revisado**; siguen abiertos y necesitan
  cambios), comenta y deja la tarjeta en la lista elegida (por defecto *PR*). Si algún PR falla, la tarjeta no se toca (reintentar
  es seguro). Las tarjetas con algún PR aprobado se marcan en verde en *Testing*
  y en el tablero. Los labels se ponen con el token personal de quien aprueba o rechaza
  (escritura de Issues y Pull requests); `GITHUB_TOKEN` solo lee los PRs.
- **Mergear PR** (vista *Testing*, aparece si algún PR tiene el label
  **Aprobado**): hace lo mismo que `scripts/rebase-approved-prs.sh` de Helios
  para los PRs de la tarjeta. Por cada PR (abierto, no borrador, no de fork y
  con el label): rebasea la rama sobre la rama a la que apunta el PR (`staging`, una
  rama de feature, etc.), la pushea con
  `--force-with-lease` y la mergea por la API con *rebase merge* y el sha
  esperado, reintentando 5 veces cada 3 s mientras GitHub recalcula. Con
  conflictos aborta el rebase y no pushea nada. El resultado por PR se ve en la app y queda como comentario e
  historial de la tarjeta. Como el script: no espera CI (si la rama está
  protegida, GitHub rechaza el merge y se reporta), no quita el label, no
  borra la rama. Si todos los PRs de la tarjeta quedan mergeados (los que ya
  lo estaban cuentan), la pasa a **Hecho**; con uno solo salteado o fallido
  se queda donde está. git corre en un directorio temporal
  por operación y dos merges del mismo repo se serializan (lock en memoria:
  vale porque el backend es un solo proceso). El rebase, el push y el merge van
  con el token personal de quien aprieta el botón (escritura de Contents y Pull
  requests); `GITHUB_TOKEN` solo lee. nginx tiene un `location` propio para
  `/api/cards/<id>/merge` con `proxy_read_timeout` largo.
- **Cargar en mi VM** (vista *Testing*): carga la rama de un PR en la VM de
  pruebas del usuario. optidev descarga la rama en su servidor
  (`PREVIEW_MIRROR_DIR`, con `GITHUB_TOKEN`; sin la config global de git, así
  no se usa otra credencial), la manda a la VM como `git bundle` por SFTP,
  hace checkout en `/opt/optidev-preview/<repo>` (conserva lo ignorado, como
  `.env`) y corre la **receta** del repo. La receta la configuran solo los
  admins en *Repositorios* (corre en las VMs de todos) y no vive en el repo.
  Ninguna credencial de GitHub llega a las VMs; la VM necesita `git`.
- **Ambiente de preview** (*Configuración*, solo admins): Vault + MySQL
  central, simulando producción. Con un token de admin **que no se guarda**,
  optidev habilita AppRole, crea el mount `preview/` y su propia AppRole
  `optidev-preview` (lo único que guarda, cifrado). Al cargar una rama, para
  cada `requirements.json` arma el `.env` del servicio con: valores de
  `preview/<repo>/<servicio>` > ambiente del usuario > `defaultValue`. El
  ambiente de cada usuario se crea solo: mount `<usuario>/`, AppRole
  `svc-<usuario>` (la que usan los servicios, con un `secret_id` por carga) y
  base `db_<usuario>` con usuario `u_<usuario>` en el MySQL central (acceso de
  admin en `preview/_platform/mysql`, credenciales en `preview/_usuarios/`).
  La AppRole de optidev puede crear mounts y políticas `svc-*`: aceptable en
  preview, no para producción.
- **Bases de preview** (menú *Bases*): lista las bases `db_<usuario>` del
  MySQL central (existe o no, tablas, tamaño y filas estimados por MySQL) y
  permite explorarlas: tablas, columnas y filas paginadas (máx. 100 por
  página, solo lectura, textos/binarios recortados). Acciones: *Crear* (lo
  mismo que hace una carga) y *Recrear vacía* (DROP + CREATE con el mismo
  usuario de MySQL; pide escribir el nombre de la base y no corre con una
  carga en curso). Cada usuario ve la suya; los admins, todas. La API recibe
  un **usuario de optidev**, nunca un nombre de base ni un host; las lecturas
  usan las credenciales `u_<usuario>` (solo tienen permisos sobre su base) en
  una transacción READ ONLY, y ninguna credencial sale del backend. No hay
  consola SQL.
- **Skills de Claude en un PR "Revisado"** (*Testing*): los PRs abiertos con
  el label *Revisado* muestran **Correr revisar-pr**, que corre `claude -p
  --dangerously-skip-permissions` en la VM de pruebas del usuario pidiéndole
  usar la skill `revisar-pr` sobre ese PR (necesita Claude Code instalado y con
  sesión en la VM: pasos 1 y 2 de *Herramientas → Claude Remote Control*, y la
  skill y `gh` disponibles allí). Corre de fondo como una tarea de la VM (log en
  vivo, una a la vez por VM, 30 min máx.) y deja un comentario en la tarjeta. El
  cliente solo elige una skill del catálogo (`SKILLS` en `skill_service.py`) y un
  PR ya registrado en la tarjeta; no manda comandos. La skill puede comentar y
  poner labels en el PR, pero el prompt le pide no mergear ni cerrar.
  Los PRs abiertos **staging → master** muestran **Preparar release** (skill
  `release`, el servidor valida contra GitHub la rama de origen y destino): en
  la VM clona el repo y, en orden, sube la versión de los servicios que
  cambiaron (en mms-backend, si cambió `shared/`, de todos), corre
  `service-requirements-scanner` para regenerar `requirements.json`, resuelve
  sus issues con `requirements-notes-resolver` y sube a staging el commit
  «Version release». Necesita el plugin `helios-service-tools` en la VM.
- **Terminal SSH** (botón *Terminal* en la VM): consola web (xterm) que queda
  en un panel flotante mientras se navega. Es un WebSocket en
  `/api/vms/{id}/terminal`; el token va en el primer mensaje, no en la URL.
  nginx necesita el bloque `location ~ ^/api/vms/[0-9]+/terminal$` con los
  headers `Upgrade` (ver `deploy/nginx/optidev.conf`); el actualizador lo
  aplica solo.
- **Rutina diaria en mi VM** (*Herramientas*): instala en la VM de pruebas un
  timer de systemd (`optidev-rutina.timer`, lunes a viernes 1:00 hora de
  Chile) que corre `claude -p` sin pedir permisos con el prompt de
  `ROUTINE_PROMPT` (`vm_doctor_service`). No usa las Routines de claude.ai:
  esas no pueden apuntar a una VM de Remote Control. *Conectar MCP* crea un
  token `odv_...` "Rutina diaria (<VM>)" y lo registra en la VM con la URL de
  `PUBLIC_URL` (o la del navegador si está vacía); reinstalar revoca el
  anterior. Al instalar se prueba llamando a `report_routine_run`. Necesita
  Claude instalado y con sesión iniciada (pasos de *Claude Remote Control*).
- **Agentes y skills de Optidev en la VM** (*Mi perfil* → VM → ícono de
  sincronizar): instala el marketplace `fmaturanaa/optidev-agents`
  (`optidev-tools`) con **todos** los plugins que declara su
  `marketplace.json`, y un timer de systemd (`optidev-agentes.timer`, todas
  las noches 0:30 hora de Chile, antes de la rutina diaria; `Persistent=true`)
  que lo actualiza solo. *Instalar / actualizar ahora* hace lo mismo a mano
  (mismo script, `AGENTS_UPDATE_SCRIPT` en `vm_doctor_service`). Hace falta
  para skills como `revisar-pr` (`skill_service`). El repo es privado: la VM
  necesita acceso a GitHub (credenciales de git o `GITHUB_TOKEN`).
- **MFA** (app de autenticación, TOTP): con `MFA_REQUIRED=true` (default),
  quien no lo tiene registra el MFA al iniciar sesión (QR + código); después
  cada ingreso pide el código. 5 códigos incorrectos seguidos lo bloquean 5
  minutos. Un admin lo rehace desde *Usuarios* (el usuario lo registra de
  nuevo al entrar). Los tokens `odv_...` de Claude Code no piden MFA. El
  secreto se cifra con `MFA_SECRET_KEY` (o una clave derivada de `JWT_SECRET`):
  cambiarla invalida todos los MFA. Si no queda ningún admin que pueda entrar,
  reiniciarlo en la base:
  `sqlite3 data/optidev.db "UPDATE users SET mfa_secret=NULL, mfa_enabled_at=NULL, mfa_last_step=NULL, mfa_failed_attempts=0, mfa_locked_until=NULL WHERE username='admin'"`.

### Despliegue sin Docker (servidor actual)

El servidor de producción no usa Docker: el backend corre con systemd
(`optidev-backend`, en `127.0.0.1:8000`) y el nginx del sistema sirve el
build del frontend desde `/var/www/optidev` y hace proxy de `/api` y `/mcp`.
La config de ese nginx está en `deploy/nginx/optidev.conf`:

```bash
cp deploy/nginx/optidev.conf /etc/nginx/sites-available/optidev
ln -sf /etc/nginx/sites-available/optidev /etc/nginx/sites-enabled/optidev
rm -f /etc/nginx/sites-enabled/default   # ocupa el 80 con default_server
nginx -t && systemctl reload nginx
```

## Actualización desde la app

Para el servidor sin Docker (backend con systemd + nginx sirviendo
`/var/www/optidev`). Los admins tienen un botón **Actualizar** arriba a la
derecha que lleva la app a la última versión de `master`.

Cómo funciona:

1. `POST /api/system/update` (solo admins) crea `data/update/request`. El
   backend **no ejecuta nada** más que crear ese archivo.
2. `optidev-update.path` (systemd) detecta el archivo y lanza
   `optidev-update.service` (root), que corre `deploy/update.sh`. El script
   borra el pedido y **ignora su contenido**: la rama (`master`) está fija.
3. El script: valida rutas → `git fetch` + fast-forward de `master` (aborta si
   hay cambios locales o commits propios) → `pip install` → respaldo de la base
   (API de backup de SQLite, en `data/backups/`, quedan los últimos 5) +
   `alembic upgrade head` → build del frontend y reemplazo de
   `/var/www/optidev` → configuración del servidor (genera
   `VM_CREDENTIALS_KEY` en el `.env` si falta y no hay credenciales guardadas;
   deja `/etc/nginx/sites-available/optidev` igual a
   `deploy/nginx/optidev.conf`, valida con `nginx -t` y recarga) →
   `systemctl restart optidev-backend` → chequeo de
   `http://127.0.0.1:$BACKEND_PORT/api/health` (directo al backend, no depende
   del puerto de nginx).
4. Si algo falla después del pull: `git reset` al commit anterior, `pip
   install`, restaurar el respaldo si ya se había migrado, volver al frontend
   y a la configuración de nginx anteriores y reiniciar.
   Los cambios a `deploy/update.sh` se aplican desde la actualización
   **siguiente** a la que los trae (corre la versión previa al pull). Los de
   `deploy/systemd/` no se instalan solos: hay que copiarlos y hacer
   `systemctl daemon-reload`.
5. Estado y log en `/var/lib/optidev-update/` (`status.json`, `update.log`,
   `update.log.1`); la app los muestra en el modal del botón.

Seguridad:

- Todo corre como **root**, igual que el backend hoy: git (sin hooks),
  pip, alembic, yarn, `systemctl` y la copia del build. El script soporta un
  usuario sin privilegios (`APP_USER`), pero el servidor no lo usa.
- El script aborta si él mismo, el repo, `.git` o el padre del web root no son
  de root o los puede escribir otro usuario **en cualquier punto de la ruta
  hasta `/`**.
- Estado, log y lock están en `/var/lib/optidev-update`. Las URLs con
  credenciales (`https://user:token@…`) se enmascaran en el log.
- Una sola corrida a la vez (lock + systemd no relanza una unidad activa). Un
  pedido que llega durante una corrida dispara otra al terminar; la API
  rechaza pedidos con uno pendiente o en curso.
- **Quien pueda mergear a `master` ejecuta código como root en el servidor**
  (incluido el propio `update.sh`, que viene del repo). Proteger la rama:
  PR con aprobación obligatoria y sin push directo.

Limitaciones:

- Las migraciones corren **con el backend viejo levantado** hasta el reinicio
  (también durante el build del frontend, ~30 s). Migraciones que solo agregan
  tablas/columnas nullable no tienen problema; las que renombran o borran
  columnas pueden hacer fallar requests en esa ventana. Esas conviene
  desplegarlas a mano (parar el backend antes).
- Si hay que restaurar el respaldo, se pierde lo escrito entre el respaldo y la
  vuelta atrás (segundos a un par de minutos).
- No hay `downgrade`: la vuelta atrás de la base es restaurar el respaldo.
- Cambios en `deploy/systemd/*` o en la config de nginx no se aplican solos:
  hay que reinstalarlos a mano.

### Instalación (una vez, como root)

Layout del servidor: repo en `/root/work/optidev`, todo de root (backend
incluido). Las unidades están en `deploy/systemd/`; si cambiás rutas,
editalas (y `PathExists=` debe coincidir con `UPDATE_REQUEST_DIR`).

```bash
cd /root/work/optidev

# El script exige estar en `master`, sin cambios locales ni archivos sin
# versionar que choquen con los del repo (p. ej. un deploy/ copiado a mano).
git status
git checkout master && git pull --ff-only

# root tiene que poder hacer `git fetch` del remoto sin interacción. Usar una
# deploy key de solo lectura, no un token personal.

# En services/backend/.env: BACKEND_PORT=8000 (lo usan la unidad del backend y
# el chequeo de salud).

install -m 644 deploy/systemd/optidev-backend.service deploy/systemd/optidev-update.service \
  deploy/systemd/optidev-update.path /etc/systemd/system/
systemctl daemon-reload
systemctl restart optidev-backend && curl -fsS http://127.0.0.1:8000/api/health
systemctl enable --now optidev-update.path

# Prueba a mano. Con el repo ya al día termina en "Ya estaba al día" sin
# rebuildear: /var/www/optidev queda como está hasta el próximo cambio en master.
systemctl start optidev-update.service
cat /var/lib/optidev-update/status.json; less /var/lib/optidev-update/update.log
```

`optidev-update.service` no se habilita: solo lo activa la `.path`.

## Conectar Claude Code

1. En la app: **Mi cuenta y tokens → Crear token**. Se muestra una sola vez.
2. Registrar el MCP (la app muestra el comando listo para copiar):

```bash
claude mcp add --transport http --scope user optidev https://<host>/mcp \
  --header "Authorization: Bearer odv_..."
```

Tools disponibles: `whoami`, `get_board_info`, `list_cards`, `get_card`,
`get_card_images` (las imágenes pegadas en la tarjeta), `get_repo_docs` (la documentación de un repo, de
`repo-docs/` del repo de agentes), `create_card`, `update_card`, `move_card`, `add_comment`, `add_pr_url`,
`archive_card`. El servidor además entrega instrucciones de uso a Claude
(mover a "En proceso" al empezar, registrar el PR y mover a "PR", no mover a
"Hecho").

Ejemplo de pedido: *"Revisá el tablero optidev y hacé las tarjetas de
Pendiente del repo helios"*.

## Importar desde Trello

1. En Trello: Menú del tablero → Imprimir, exportar y compartir → Exportar como JSON.
2. Cargar en optidev los repositorios con las **mismas keys que las etiquetas
   de Trello** (así las etiquetas se convierten en repositorios).
3. Crear un token y correr:

```bash
python3 scripts/import_trello.py trello.json --url https://<host> --token odv_... --dry-run
python3 scripts/import_trello.py trello.json --url https://<host> --token odv_...
```

Ver el docstring del script para el detalle (mapeo de listas, comentarios,
PRs). No es idempotente: correrlo dos veces duplica tarjetas.

## API

Documentación OpenAPI en `/api/docs`. Las respuestas siguen el formato
`{"success": true, "data": ..., "message": ...}`; los errores, `{"detail": "..."}`.
La API también acepta tokens `odv_…` (los cambios quedan con `via=api`).
