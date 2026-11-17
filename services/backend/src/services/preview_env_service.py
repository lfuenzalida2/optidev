"""Ambiente de preview de cada usuario: Vault + MySQL central + `.env` de los servicios.

Simula producción (vault0 + vault1 + MySQL):
  - Cada usuario de optidev tiene un **mount KV v2 con su nombre de usuario**
    (sus dispositivos, como el vault1 de producción) y una **base
    `db_<usuario>`** con su propio usuario de MySQL.
  - Los servicios leen el Vault en ejecución con una AppRole por mount,
    `svc-<usuario>` (como Helios en producción: una por mount).
  - Los valores de los `.env` de preview están en el mount `preview/`:
      preview/<repo>/<servicio>   valores de ese servicio
      preview/_platform/mysql     host, port, admin_user, admin_password
      preview/_usuarios/<usuario> credenciales de MySQL de cada usuario
    Lo compartido por todos (ARI_HOST/USERNAME/PASSWORD, DB_INTERCOM_* del
    Asterisk) va en preview/<repo>/<servicio>. ARI_APP_NAME no: lo pone
    optidev por usuario (`routing-app-<usuario>`).

optidev entra al Vault con su propia AppRole (`optidev-preview`), que crea
`connect()` con un token de admin una sola vez: el token no se guarda.

**Ojo con el alcance de esa AppRole:** puede crear mounts y escribir políticas
`svc-*` y AppRoles `svc-*`, así que quien tenga sus credenciales puede darse
más permisos en el Vault. Es aceptable en el ambiente de preview; en producción
no se usaría así.
"""

import asyncio
import re
import secrets
import time

import pymysql
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import AsyncSessionLocal
from src.models.app_setting import AppSetting
from src.services.errors import ServiceError
from src.services.secret_box import decrypt_json, encrypt_json
from src.services.vault_client import VaultClient, VaultError

SETTING_KEY = "preview_vault"
PREVIEW_MOUNT = "preview"
OPTIDEV_ROLE = "optidev-preview"
USERNAME_RE = re.compile(r"^[a-z0-9_]{1,30}$")

OPTIDEV_POLICY = f"""
# optidev: leer/escribir los valores de preview
path "{PREVIEW_MOUNT}/data/*"     {{ capabilities = ["create", "read", "update", "list"] }}
path "{PREVIEW_MOUNT}/metadata/*" {{ capabilities = ["read", "list"] }}
# crear el mount de cada usuario
path "sys/mounts"   {{ capabilities = ["read"] }}
path "sys/mounts/*" {{ capabilities = ["create", "read", "update"] }}
# AppRole svc-<usuario> de los servicios de cada usuario
path "sys/policies/acl/svc-*"             {{ capabilities = ["create", "read", "update"] }}
path "auth/approle/role/svc-*"            {{ capabilities = ["create", "read", "update"] }}
path "auth/approle/role/svc-*/role-id"    {{ capabilities = ["read"] }}
path "auth/approle/role/svc-*/secret-id"  {{ capabilities = ["create", "update"] }}
"""


def _service_policy(mount: str) -> str:
    return (f'path "{mount}/data/*" {{ capabilities = ["read", "list"] }}\n'
            f'path "{mount}/metadata/*" {{ capabilities = ["read", "list"] }}\n')


# --- Configuración guardada -----------------------------------------------------

async def _load_setting(db: AsyncSession) -> dict | None:
    row = await db.get(AppSetting, SETTING_KEY)
    return decrypt_json(row.value_enc) if row else None


async def _save_setting(db: AsyncSession, value: dict) -> None:
    row = await db.get(AppSetting, SETTING_KEY)
    if row:
        row.value_enc = encrypt_json(value)
    else:
        db.add(AppSetting(key=SETTING_KEY, value_enc=encrypt_json(value)))
    await db.commit()


_token_cache: dict = {"key": None, "token": None, "until": 0.0}


async def client(db: AsyncSession | None = None) -> VaultClient:
    """Cliente autenticado con la AppRole de optidev (token cacheado ~50 min)."""
    if db is None:
        async with AsyncSessionLocal() as own:
            setting = await _load_setting(own)
    else:
        setting = await _load_setting(db)
    if not setting:
        raise ServiceError("El Vault de preview no está conectado (Configuración → Ambiente de preview)", 409)
    vault = VaultClient(setting["addr"])
    key = (setting["addr"], setting["roleId"])
    if _token_cache["key"] == key and time.monotonic() < _token_cache["until"]:
        vault.token = _token_cache["token"]
        return vault
    await vault.login_approle(setting["roleId"], setting["secretId"])
    _token_cache.update(key=key, token=vault.token, until=time.monotonic() + 50 * 60)
    return vault


# --- Conectar y estado ----------------------------------------------------------

async def connect(db: AsyncSession, addr: str, admin_token: str) -> dict:
    """Configura el Vault (idempotente) con un token de admin y guarda solo la
    AppRole de optidev. El token de admin no se guarda."""
    addr = (addr or "").strip().rstrip("/")
    if not addr.startswith(("http://", "https://")):
        raise ServiceError("La dirección del Vault tiene que empezar con http:// o https://")
    admin = VaultClient(addr, admin_token.strip())
    steps = []
    if await admin.ensure_approle():
        steps.append("habilité AppRole")
    if await admin.ensure_kv2(PREVIEW_MOUNT):
        steps.append(f"creé el mount {PREVIEW_MOUNT}/")
    await admin.write_policy(OPTIDEV_ROLE, OPTIDEV_POLICY)
    await admin.write_role(OPTIDEV_ROLE, token_policies=[OPTIDEV_ROLE], token_ttl="1h", token_max_ttl="4h",
                           secret_id_ttl="0")
    role_id = await admin.role_id(OPTIDEV_ROLE)
    secret_id = await admin.new_secret_id(OPTIDEV_ROLE, {"source": "optidev"})
    # Se prueba la AppRole antes de guardarla.
    await VaultClient(addr).login_approle(role_id, secret_id)
    await _save_setting(db, {"addr": addr, "roleId": role_id, "secretId": secret_id})
    _token_cache.update(key=None)
    steps.append(f"AppRole {OPTIDEV_ROLE} lista")
    return {**await status(db), "steps": steps}


async def status(db: AsyncSession) -> dict:
    setting = await _load_setting(db)
    if not setting:
        return {"connected": False, "addr": None, "mysql": None, "error": None}
    result = {"connected": True, "addr": setting["addr"], "mysql": None, "error": None}
    try:
        vault = await client(db)
        mysql = await vault.kv_read(PREVIEW_MOUNT, "_platform/mysql")
        if mysql:
            result["mysql"] = {"host": mysql.get("host"), "port": int(mysql.get("port") or 3306),
                               "adminUser": mysql.get("admin_user")}
    except ServiceError as exc:
        result["error"] = exc.message
    return result


async def set_mysql(db: AsyncSession, host: str, port: int, admin_user: str, admin_password: str | None) -> dict:
    """Guarda en el Vault el acceso de admin al MySQL central. Contraseña vacía
    = se conserva la guardada."""
    vault = await client(db)
    current = await vault.kv_read(PREVIEW_MOUNT, "_platform/mysql") or {}
    password = admin_password or current.get("admin_password")
    if not password:
        raise ServiceError("Falta la contraseña del admin de MySQL")
    values = {"host": host.strip(), "port": str(port), "admin_user": admin_user.strip(), "admin_password": password}
    await asyncio.to_thread(_mysql_ping, values)
    await vault.kv_write(PREVIEW_MOUNT, "_platform/mysql", values)
    return await status(db)


# --- MySQL ------------------------------------------------------------------------

def _mysql_connect(platform: dict):
    try:
        return pymysql.connect(host=platform["host"], port=int(platform.get("port") or 3306),
                               user=platform["admin_user"], password=platform["admin_password"],
                               connect_timeout=10, autocommit=True)
    except pymysql.MySQLError as exc:
        raise ServiceError(f"No se pudo conectar al MySQL central {platform.get('host')}: {exc.args[-1]}", 502) from exc


def _mysql_ping(platform: dict) -> None:
    _mysql_connect(platform).close()


def _mysql_provision(platform: dict, database: str, user: str, password: str) -> None:
    """Base + usuario con todos los permisos sobre ella. Idempotente: si el
    usuario ya existía, le deja la contraseña guardada en el Vault."""
    conn = _mysql_connect(platform)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}`")
            cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (user, password))
            cur.execute("ALTER USER %s@'%%' IDENTIFIED BY %s", (user, password))
            cur.execute(f"GRANT ALL PRIVILEGES ON `{database}`.* TO %s@'%%'", (user,))
    except pymysql.MySQLError as exc:
        raise ServiceError(f"MySQL: {exc.args[-1]}", 502) from exc
    finally:
        conn.close()


# --- Ambiente de un usuario ---------------------------------------------------------

async def ensure_user(username: str, log=None) -> dict:
    """Deja listo el ambiente del usuario (idempotente) y devuelve el contexto
    para sus `.env`: base de datos y acceso al Vault con `svc-<usuario>`."""
    if not USERNAME_RE.match(username):
        raise ServiceError(f"El usuario '{username}' no sirve como nombre de mount/base (solo a-z, 0-9 y _)")

    async def note(text: str):
        if log:
            await log.write(text + "\n")

    vault = await client()
    if await vault.ensure_kv2(username):
        await note(f"Creé el mount {username}/ en el Vault")
    role = f"svc-{username}"
    await vault.write_policy(role, _service_policy(username))
    await vault.write_role(role, token_policies=[role], token_ttl="1h", token_max_ttl="24h", secret_id_ttl="0")

    platform = await vault.kv_read(PREVIEW_MOUNT, "_platform/mysql")
    if not platform:
        raise ServiceError("Falta configurar el MySQL central (Configuración → Ambiente de preview)")
    database, db_user = f"db_{username}", f"u_{username}"
    creds = await vault.kv_read(PREVIEW_MOUNT, f"_usuarios/{username}")
    if not creds:
        creds = {"db_user": db_user, "db_password": secrets.token_urlsafe(24)}
    await asyncio.to_thread(_mysql_provision, platform, database, creds["db_user"], creds["db_password"])
    await vault.kv_write(PREVIEW_MOUNT, f"_usuarios/{username}", creds)
    await note(f"Base {database} y usuario de MySQL {creds['db_user']} listos")

    return {
        "DB_HOST": platform["host"],
        "DB_PORT": str(platform.get("port") or 3306),
        "DB_NAME": database,
        "DB_USERNAME": creds["db_user"],
        "DB_PASSWORD": creds["db_password"],
        "VAULT_URL": vault.addr,
        "VAULT_MOUNT_POINT": username,
        "VAULT_ROLE_ID": await vault.role_id(role),
        # Asterisk compartido: cada usuario escucha su propia app de ARI, así
        # sus llamadas no le llegan a otro (ver ROUTING_APP en el dialplan).
        "ARI_APP_NAME": f"routing-app-{username}",
    }


async def new_service_secret(username: str, metadata: dict) -> str:
    """Un secret_id de `svc-<usuario>` para una carga (queda en el .env de la VM)."""
    return await (await client()).new_secret_id(f"svc-{username}", metadata)


async def service_values(repo_key: str, service: str) -> dict:
    return await (await client()).kv_read(PREVIEW_MOUNT, f"{repo_key}/{service}") or {}


# --- Armado de los .env ----------------------------------------------------------------

def compute_env(variables: list[dict], vault_values: dict, context: dict) -> tuple[dict, dict, list[str]]:
    """Valor de cada variable declarada en requirements.json, por prioridad:
    Vault (preview/<repo>/<servicio>) > ambiente del usuario > defaultValue.
    Devuelve (env, cuántas de cada origen, obligatorias que faltan)."""
    env, origin = {}, {"vault": 0, "usuario": 0, "default": 0}
    missing = []
    for var in variables:
        name = var.get("name")
        if not name:
            continue
        if vault_values.get(name) not in (None, ""):
            env[name], source = str(vault_values[name]), "vault"
        elif name in context:
            env[name], source = str(context[name]), "usuario"
        elif var.get("defaultValue") not in (None, ""):
            env[name], source = str(var["defaultValue"]), "default"
        else:
            if var.get("required"):
                missing.append(name)
            continue
        origin[source] += 1
    return env, origin, missing


def render_env(env: dict) -> str:
    """Formato de env_file de docker compose: KEY=valor, sin comillas."""
    lines = []
    for name, value in env.items():
        if "\n" in value:
            raise VaultError(f"La variable {name} tiene saltos de línea; no entra en un .env")
        lines.append(f"{name}={value}")
    return "\n".join(lines) + "\n"
