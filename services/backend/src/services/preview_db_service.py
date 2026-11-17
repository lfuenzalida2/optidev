"""Visualizador de las bases de preview (`db_<usuario>` en el MySQL central).

Lo que optidev gestiona (ver preview_env_service.ensure_user): una base
`db_<usuario>` por usuario de optidev, con su propio usuario de MySQL
`u_<usuario>`. Este módulo permite verlas (listado con tamaño, tablas,
columnas y filas paginadas) y dos acciones: preparar la base (la misma que
hace una carga) y recrearla vacía.

Reglas de seguridad:
  - El cliente nunca manda un nombre de base ni un host: manda un **usuario de
    optidev**. La base sale de ese usuario (`db_<usuario>`) y el host del
    MySQL central guardado en el Vault (`preview/_platform/mysql`).
  - Cada usuario ve solo la suya; los admins, todas.
  - Las lecturas (tablas, columnas, filas) se hacen con las credenciales del
    usuario dueño (`u_<usuario>`), que solo tiene permisos sobre su base: aunque
    hubiera un error acá, no puede leer otra base. Además van en una
    transacción READ ONLY, con límite de filas y de tiempo.
  - Las credenciales nunca se devuelven: solo host y puerto.
  - Sin consola SQL: las únicas consultas son las de este módulo, y el nombre
    de tabla tiene que existir en `information_schema` de esa base.
"""

import asyncio
import datetime as dt
import decimal
import re

import pymysql
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.preview_load import PreviewLoad
from src.models.user import User
from src.services import preview_env_service
from src.services.auth_service import Actor
from src.services.errors import ConflictError, NotFoundError, ServiceError

MAX_PAGE_SIZE = 100
MAX_OFFSET = 100_000
MAX_TEXT = 500          # caracteres de un valor de texto que se devuelven
MAX_BINARY = 64         # bytes de un valor binario que se muestran (en hex)
QUERY_TIMEOUT_MS = 10_000
_TABLE = re.compile(r"^[^\x00`]{1,64}$")
_JS_SAFE_INT = 2**53 - 1


def database_for(username: str) -> str:
    return f"db_{username}"


# --- Permisos y validación ---------------------------------------------------------

def can_access(actor: Actor, username: str) -> bool:
    return actor.user.role == "admin" or actor.user.username == username


async def resolve_owner(db: AsyncSession, actor: Actor, username: str) -> User:
    """Usuario de optidev dueño de la base. Falla si el nombre no es uno que
    optidev gestione o si el actor no tiene acceso. A un no-admin se le responde
    404 también cuando el usuario existe, para no revelar quién tiene base."""
    if not preview_env_service.USERNAME_RE.match(username or ""):
        raise NotFoundError("Base de preview no encontrada")
    if not can_access(actor, username):
        raise NotFoundError("Base de preview no encontrada")
    user = (await db.execute(select(User).where(User.username == username, User.deleted_at.is_(None)))).scalar_one_or_none()
    if not user:
        raise NotFoundError("Base de preview no encontrada")
    return user


def check_table_name(table: str, existing: list[str]) -> str:
    """El nombre de tabla tiene que ser exactamente uno de los de la base."""
    if not table or not _TABLE.match(table) or table not in existing:
        raise NotFoundError(f"La tabla '{table}' no existe en la base")
    return table


def quote_ident(name: str) -> str:
    if not _TABLE.match(name):
        raise ServiceError("Identificador inválido")
    return "`" + name.replace("`", "``") + "`"


# --- Valores -----------------------------------------------------------------------

def serialize_value(value):
    """Valor de una celda → algo que viaje bien en JSON y no sea enorme."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= _JS_SAFE_INT else str(value)
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        shown = raw[:MAX_BINARY].hex()
        return f"0x{shown}{'…' if len(raw) > MAX_BINARY else ''} ({len(raw)} bytes)"
    text = str(value)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + f"… ({len(text)} caracteres)"


# --- MySQL (sincrónico, se llama con asyncio.to_thread) ----------------------------

def _connect(host: str, port: int, user: str, password: str):
    try:
        return pymysql.connect(host=host, port=int(port or 3306), user=user, password=password,
                               connect_timeout=10, read_timeout=30, write_timeout=30, autocommit=True)
    except pymysql.MySQLError as exc:
        raise ServiceError(f"No se pudo conectar al MySQL central {host}: {exc.args[-1]}", 502) from exc


def _admin_connect(platform: dict):
    return _connect(platform["host"], platform.get("port"), platform["admin_user"], platform["admin_password"])


def _limit_time(cur) -> None:
    # MySQL (max_execution_time, ms) o MariaDB (max_statement_time, s); el otro falla y se ignora.
    for statement in (f"SET SESSION max_execution_time = {QUERY_TIMEOUT_MS}",
                      f"SET SESSION max_statement_time = {QUERY_TIMEOUT_MS / 1000}"):
        try:
            cur.execute(statement)
        except pymysql.MySQLError:
            pass


def _mysql_summary(platform: dict, databases: list[str]) -> dict:
    """{base: {tables, sizeBytes, rowsEstimate}} de las que existen."""
    if not databases:
        return {}
    conn = _admin_connect(platform)
    try:
        with conn.cursor() as cur:
            marks = ", ".join(["%s"] * len(databases))
            cur.execute(
                "SELECT s.SCHEMA_NAME, COUNT(t.TABLE_NAME), "
                "COALESCE(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0), COALESCE(SUM(t.TABLE_ROWS), 0) "
                "FROM information_schema.SCHEMATA s "
                "LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = s.SCHEMA_NAME "
                f"WHERE s.SCHEMA_NAME IN ({marks}) GROUP BY s.SCHEMA_NAME", databases)
            return {name: {"tables": int(tables), "sizeBytes": int(size), "rowsEstimate": int(rows)}
                    for name, tables, size, rows in cur.fetchall()}
    except pymysql.MySQLError as exc:
        raise ServiceError(f"MySQL: {exc.args[-1]}", 502) from exc
    finally:
        conn.close()


def _mysql_read(creds: dict, database: str, fn):
    """Corre `fn(cur)` con el usuario dueño de la base, en una transacción READ ONLY."""
    conn = _connect(creds["host"], creds["port"], creds["db_user"], creds["db_password"])
    try:
        with conn.cursor() as cur:
            _limit_time(cur)
            cur.execute("START TRANSACTION READ ONLY")
            try:
                return fn(cur)
            finally:
                cur.execute("ROLLBACK")
    except pymysql.MySQLError as exc:
        raise ServiceError(f"MySQL ({database}): {exc.args[-1]}", 502) from exc
    finally:
        conn.close()


def _mysql_tables(creds: dict, database: str) -> list[dict]:
    def run(cur):
        cur.execute(
            "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, DATA_LENGTH + INDEX_LENGTH, TABLE_COLLATION "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME", (database,))
        return [{"name": name, "type": "view" if kind == "VIEW" else "table", "engine": engine,
                 "rowsEstimate": int(rows) if rows is not None else None,
                 "sizeBytes": int(size) if size is not None else None, "collation": collation}
                for name, kind, engine, rows, size, collation in cur.fetchall()]
    return _mysql_read(creds, database, run)


def _mysql_columns(creds: dict, database: str, table: str) -> list[dict]:
    def run(cur):
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION", (database, table))
        return [{"name": name, "type": ctype, "nullable": nullable == "YES", "key": key or None,
                 "default": serialize_value(default), "extra": extra or None}
                for name, ctype, nullable, key, default, extra in cur.fetchall()]
    return _mysql_read(creds, database, run)


def rows_query(database: str, table: str, order_by: list[str], limit: int, offset: int) -> tuple[str, tuple]:
    sql = f"SELECT * FROM {quote_ident(database)}.{quote_ident(table)}"
    if order_by:
        sql += " ORDER BY " + ", ".join(quote_ident(c) for c in order_by)
    return sql + " LIMIT %s OFFSET %s", (limit, offset)


def _mysql_rows(creds: dict, database: str, table: str, order_by: list[str], limit: int, offset: int) -> dict:
    def run(cur):
        sql, params = rows_query(database, table, order_by, limit + 1, offset)
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
        return {"columns": names, "rows": rows[:limit], "hasMore": len(rows) > limit}
    return _mysql_read(creds, database, run)


def _mysql_drop(platform: dict, database: str) -> None:
    conn = _admin_connect(platform)
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {quote_ident(database)}")
    except pymysql.MySQLError as exc:
        raise ServiceError(f"MySQL: {exc.args[-1]}", 502) from exc
    finally:
        conn.close()


# --- Acceso al Vault ------------------------------------------------------------------

async def _platform(db: AsyncSession) -> dict:
    vault = await preview_env_service.client(db)
    platform = await vault.kv_read(preview_env_service.PREVIEW_MOUNT, "_platform/mysql")
    if not platform:
        raise ServiceError("Falta configurar el MySQL central (Configuración → Ambiente de preview)", 409)
    return platform


async def _owner_creds(db: AsyncSession, username: str) -> dict:
    """Credenciales del dueño de la base (solo uso interno, nunca salen de acá)."""
    vault = await preview_env_service.client(db)
    platform = await _platform(db)
    creds = await vault.kv_read(preview_env_service.PREVIEW_MOUNT, f"_usuarios/{username}")
    if not creds:
        raise ConflictError(f"La base {database_for(username)} todavía no está preparada")
    return {"host": platform["host"], "port": int(platform.get("port") or 3306),
            "db_user": creds["db_user"], "db_password": creds["db_password"]}


async def _running_load(db: AsyncSession, user_id: int) -> bool:
    found = await db.execute(select(PreviewLoad.id).where(PreviewLoad.user_id == user_id,
                                                          PreviewLoad.status == "running").limit(1))
    return found.first() is not None


# --- API del servicio -------------------------------------------------------------------

async def list_databases(db: AsyncSession, actor: Actor) -> dict:
    query = select(User).where(User.deleted_at.is_(None)).order_by(User.username)
    if actor.user.role != "admin":
        query = query.where(User.id == actor.user.id)
    users = [u for u in (await db.execute(query)).scalars()
             if preview_env_service.USERNAME_RE.match(u.username)]
    running = set((await db.execute(select(PreviewLoad.user_id).where(PreviewLoad.status == "running"))).scalars())

    result = {"mysql": None, "error": None, "databases": []}
    summary = None
    try:
        platform = await _platform(db)
        result["mysql"] = {"host": platform["host"], "port": int(platform.get("port") or 3306)}
        summary = await asyncio.to_thread(_mysql_summary, platform, [database_for(u.username) for u in users])
    except ServiceError as exc:
        result["error"] = exc.message

    for user in users:
        name = database_for(user.username)
        info = (summary or {}).get(name)
        result["databases"].append({
            "username": user.username, "name": user.name, "database": name,
            "mine": user.id == actor.user.id,
            # None = no se pudo consultar el MySQL.
            "exists": None if summary is None else info is not None,
            "tables": info["tables"] if info else None,
            "sizeBytes": info["sizeBytes"] if info else None,
            "rowsEstimate": info["rowsEstimate"] if info else None,
            "loadRunning": user.id in running,
        })
    return result


async def list_tables(db: AsyncSession, actor: Actor, username: str) -> dict:
    owner = await resolve_owner(db, actor, username)
    creds = await _owner_creds(db, owner.username)
    database = database_for(owner.username)
    tables = await asyncio.to_thread(_mysql_tables, creds, database)
    return {"username": owner.username, "database": database, "tables": tables}


async def _checked_table(db: AsyncSession, actor: Actor, username: str, table: str):
    owner = await resolve_owner(db, actor, username)
    creds = await _owner_creds(db, owner.username)
    database = database_for(owner.username)
    tables = await asyncio.to_thread(_mysql_tables, creds, database)
    check_table_name(table, [t["name"] for t in tables])
    return owner, creds, database


async def table_detail(db: AsyncSession, actor: Actor, username: str, table: str) -> dict:
    owner, creds, database = await _checked_table(db, actor, username, table)
    columns = await asyncio.to_thread(_mysql_columns, creds, database, table)
    return {"username": owner.username, "database": database, "table": table, "columns": columns}


async def table_rows(db: AsyncSession, actor: Actor, username: str, table: str, page: int, page_size: int) -> dict:
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ServiceError(f"Página inválida (tamaño máximo {MAX_PAGE_SIZE})")
    offset = (page - 1) * page_size
    if offset > MAX_OFFSET:
        raise ServiceError(f"Solo se pueden ver las primeras {MAX_OFFSET} filas")
    owner, creds, database = await _checked_table(db, actor, username, table)
    columns = await asyncio.to_thread(_mysql_columns, creds, database, table)
    # Orden estable para paginar: la clave primaria si hay.
    order_by = [c["name"] for c in columns if c["key"] == "PRI"]
    data = await asyncio.to_thread(_mysql_rows, creds, database, table, order_by, page_size, offset)
    return {"username": owner.username, "database": database, "table": table, "page": page,
            "pageSize": page_size, "orderBy": order_by, "columns": data["columns"],
            "rows": [[serialize_value(v) for v in row] for row in data["rows"]], "hasMore": data["hasMore"]}


async def prepare(db: AsyncSession, actor: Actor, username: str) -> dict:
    """Crea (o deja como está) la base del usuario: lo mismo que hace una carga."""
    owner = await resolve_owner(db, actor, username)
    context = await preview_env_service.ensure_user(owner.username)
    return {"username": owner.username, "database": context["DB_NAME"]}


async def reset(db: AsyncSession, actor: Actor, username: str, confirm: str) -> dict:
    """Borra la base y la vuelve a crear vacía (mismo usuario y contraseña de
    MySQL, así los .env ya cargados en la VM siguen sirviendo)."""
    owner = await resolve_owner(db, actor, username)
    database = database_for(owner.username)
    if confirm != database:
        raise ServiceError(f"Para confirmar escribí el nombre de la base: {database}")
    if await _running_load(db, owner.id):
        raise ConflictError("Hay una carga en curso en la VM de ese usuario; esperá a que termine")
    platform = await _platform(db)
    await asyncio.to_thread(_mysql_drop, platform, database)
    context = await preview_env_service.ensure_user(owner.username)
    return {"username": owner.username, "database": context["DB_NAME"]}
