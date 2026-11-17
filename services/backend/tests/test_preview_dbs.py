import datetime as dt
import decimal
import json

import pytest

from src.services import preview_db_service as svc
from src.services.errors import NotFoundError, ServiceError
from tests.test_preview_env import _connect, mysql, vault  # noqa: F401 — fixtures


class FakeMysql:
    """MySQL central en memoria. Como el real, un usuario `u_<x>` solo puede
    leer `db_<x>`: si optidev usara otras credenciales, los tests fallan."""

    def __init__(self):
        self.dbs = {
            "db_admin": {"clientes": {"columns": [("id", "int", "PRI"), ("nombre", "varchar(100)", None)],
                                      "rows": [[i, f"cliente {i}"] for i in range(1, 8)]}},
            "db_dev": {"edificios": {"columns": [("id", "int", "PRI"), ("direccion", "text", None)],
                                     "rows": [[1, "Av. Siempre Viva 742"]]}},
        }
        self.calls = []

    def _check(self, creds, database):
        assert "host" in creds and creds["host"] == "mysql-central"
        if creds["db_user"] != "u_" + database.removeprefix("db_"):
            raise ServiceError(f"MySQL ({database}): Access denied for user '{creds['db_user']}'", 502)

    def summary(self, platform, databases):
        assert platform["admin_user"] == "root"
        self.calls.append(("summary", tuple(databases)))
        return {name: {"tables": len(self.dbs[name]), "sizeBytes": 16384, "rowsEstimate": 7}
                for name in databases if name in self.dbs}

    def tables(self, creds, database):
        self._check(creds, database)
        self.calls.append(("tables", database))
        return [{"name": t, "type": "table", "engine": "InnoDB", "rowsEstimate": len(v["rows"]),
                 "sizeBytes": 16384, "collation": "utf8mb4_general_ci"} for t, v in self.dbs.get(database, {}).items()]

    def columns(self, creds, database, table):
        self._check(creds, database)
        self.calls.append(("columns", database, table))
        return [{"name": n, "type": t, "nullable": False, "key": k, "default": None, "extra": None}
                for n, t, k in self.dbs[database][table]["columns"]]

    def rows(self, creds, database, table, order_by, limit, offset):
        self._check(creds, database)
        self.calls.append(("rows", database, table, tuple(order_by), limit, offset))
        data = self.dbs[database][table]
        rows = data["rows"][offset:offset + limit + 1]
        return {"columns": [c[0] for c in data["columns"]], "rows": rows[:limit], "hasMore": len(rows) > limit}

    def drop(self, platform, database):
        self.calls.append(("drop", database))
        self.dbs.pop(database, None)


@pytest.fixture
def fake_mysql(monkeypatch, mysql):  # noqa: F811
    fake = FakeMysql()
    monkeypatch.setattr(svc, "_mysql_summary", fake.summary)
    monkeypatch.setattr(svc, "_mysql_tables", fake.tables)
    monkeypatch.setattr(svc, "_mysql_columns", fake.columns)
    monkeypatch.setattr(svc, "_mysql_rows", fake.rows)
    monkeypatch.setattr(svc, "_mysql_drop", fake.drop)
    return fake


async def _member(admin, username="dev"):
    await admin.post("/api/users", json={"username": username, "email": f"{username}@optidev.com",
                                         "name": username.title(), "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": username, "password": "dev-pass-123"})
    return {"Authorization": f"Bearer {login.json()['data']['token']}"}


async def _setup(admin):
    await _connect(admin)
    dev = await _member(admin)
    assert (await admin.post("/api/preview-dbs/admin/prepare")).status_code == 200
    resp = await admin.post("/api/preview-dbs/dev/prepare", headers=dev)
    assert resp.status_code == 200, resp.text
    return dev


async def test_list_own_or_all(admin, vault, fake_mysql):  # noqa: F811
    dev = await _setup(admin)

    mine = (await admin.get("/api/preview-dbs", headers=dev)).json()["data"]
    assert [d["database"] for d in mine["databases"]] == ["db_dev"]
    assert mine["databases"][0] | {"loadRunning": False} == {
        "username": "dev", "name": "Dev", "database": "db_dev", "mine": True, "exists": True, "tables": 1,
        "sizeBytes": 16384, "rowsEstimate": 7, "loadRunning": False}
    assert mine["mysql"] == {"host": "mysql-central", "port": 3306}

    everything = (await admin.get("/api/preview-dbs")).json()["data"]
    assert [d["database"] for d in everything["databases"]] == ["db_admin", "db_dev"]
    assert [d["mine"] for d in everything["databases"]] == [True, False]

    # Ninguna credencial (ni la del admin de MySQL ni las de cada usuario) llega al cliente.
    passwords = ["clave-admin-mysql"] + [v["db_password"] for (m, k), v in vault.kv.items() if k.startswith("_usuarios/")]
    assert len(passwords) == 3
    for body in (mine, everything):
        text = json.dumps(body)
        assert not any(p in text for p in passwords) and "db_password" not in text and "admin_user" not in text


async def test_member_cannot_touch_other_databases(admin, vault, fake_mysql):  # noqa: F811
    dev = await _setup(admin)
    before = list(fake_mysql.calls)
    for method, url, body in [
        ("get", "/api/preview-dbs/admin/tables", None),
        ("get", "/api/preview-dbs/admin/columns?table=clientes", None),
        ("get", "/api/preview-dbs/admin/rows?table=clientes", None),
        ("post", "/api/preview-dbs/admin/prepare", None),
        ("post", "/api/preview-dbs/admin/reset", {"confirm": "db_admin"}),
    ]:
        resp = await admin.request(method.upper(), url, json=body, headers=dev)
        assert resp.status_code == 404, (url, resp.text)
    # Ni siquiera se consultó el MySQL.
    assert fake_mysql.calls == before
    assert "db_admin" in fake_mysql.dbs

    # El admin sí entra a la del dev (con las credenciales del dev, no las suyas).
    resp = await admin.get("/api/preview-dbs/dev/tables")
    assert resp.status_code == 200 and [t["name"] for t in resp.json()["data"]["tables"]] == ["edificios"]


@pytest.mark.parametrize("username", ["db_admin", "Admin", "admin;drop", "..%2Fadmin", "nadie", "a" * 31, "information_schema"])
async def test_only_optidev_users_databases(admin, vault, fake_mysql, username):  # noqa: F811
    await _setup(admin)
    before = list(fake_mysql.calls)
    resp = await admin.get(f"/api/preview-dbs/{username}/tables")
    assert resp.status_code == 404
    resp = await admin.post(f"/api/preview-dbs/{username}/reset", json={"confirm": f"db_{username}"})
    assert resp.status_code == 404
    assert fake_mysql.calls == before


async def test_deleted_user_database_is_not_reachable(admin, vault, fake_mysql):  # noqa: F811
    await _setup(admin)
    users = (await admin.get("/api/users")).json()["data"]
    dev_id = next(u["id"] for u in users if u["username"] == "dev")
    assert (await admin.delete(f"/api/users/{dev_id}")).status_code in (200, 204)
    assert (await admin.get("/api/preview-dbs/dev/tables")).status_code == 404
    listed = (await admin.get("/api/preview-dbs")).json()["data"]["databases"]
    assert [d["username"] for d in listed] == ["admin"]


async def test_browse_rows(admin, vault, fake_mysql):  # noqa: F811
    await _setup(admin)
    cols = (await admin.get("/api/preview-dbs/admin/columns", params={"table": "clientes"})).json()["data"]
    assert [c["name"] for c in cols["columns"]] == ["id", "nombre"]

    page = (await admin.get("/api/preview-dbs/admin/rows",
                            params={"table": "clientes", "page": 2, "pageSize": 3})).json()["data"]
    assert page["rows"] == [[4, "cliente 4"], [5, "cliente 5"], [6, "cliente 6"]]
    assert page["hasMore"] is True and page["orderBy"] == ["id"] and page["columns"] == ["id", "nombre"]
    assert ("rows", "db_admin", "clientes", ("id",), 3, 3) in fake_mysql.calls
    last = (await admin.get("/api/preview-dbs/admin/rows",
                            params={"table": "clientes", "page": 3, "pageSize": 3})).json()["data"]
    assert last["rows"] == [[7, "cliente 7"]] and last["hasMore"] is False

    # Límite de filas por página.
    assert (await admin.get("/api/preview-dbs/admin/rows",
                            params={"table": "clientes", "pageSize": 101})).status_code == 422
    # Tablas que no existen en ESA base (aunque existan en otra) o con nombres raros.
    for table in ["edificios", "clientes`; DROP DATABASE db_admin; --", "", "information_schema.TABLES"]:
        resp = await admin.get("/api/preview-dbs/admin/rows", params={"table": table})
        assert resp.status_code in (404, 422), table
    assert not any(c[0] == "rows" and c[2] != "clientes" for c in fake_mysql.calls)


async def test_browse_needs_prepared_database(admin, vault, fake_mysql):  # noqa: F811
    await _connect(admin)
    resp = await admin.get("/api/preview-dbs/admin/tables")
    assert resp.status_code == 409 and "no está preparada" in resp.json()["detail"]


async def test_reset(admin, vault, fake_mysql, mysql, monkeypatch):  # noqa: F811
    dev = await _setup(admin)
    provisioned = [c for c in mysql if c[0] == "provision" and c[1] == "db_dev"][-1]

    resp = await admin.post("/api/preview-dbs/dev/reset", json={"confirm": "db_admin"}, headers=dev)
    assert resp.status_code == 400 and "db_dev" in resp.json()["detail"]
    assert ("drop", "db_dev") not in fake_mysql.calls

    async def busy(db, user_id):
        return True
    original = svc._running_load
    monkeypatch.setattr(svc, "_running_load", busy)
    resp = await admin.post("/api/preview-dbs/dev/reset", json={"confirm": "db_dev"}, headers=dev)
    assert resp.status_code == 409 and "carga en curso" in resp.json()["detail"]
    monkeypatch.setattr(svc, "_running_load", original)

    resp = await admin.post("/api/preview-dbs/dev/reset", json={"confirm": "db_dev"}, headers=dev)
    assert resp.status_code == 200, resp.text
    assert fake_mysql.calls[-1] == ("drop", "db_dev") and "db_dev" not in fake_mysql.dbs
    # Se recrea con el mismo usuario y contraseña de MySQL (los .env de la VM siguen sirviendo).
    assert [c for c in mysql if c[0] == "provision"][-1] == provisioned
    assert "db_admin" in fake_mysql.dbs


async def test_list_without_preview_env(admin, vault, fake_mysql):  # noqa: F811
    data = (await admin.get("/api/preview-dbs")).json()["data"]
    assert "no está conectado" in data["error"]
    assert data["databases"][0]["database"] == "db_admin" and data["databases"][0]["exists"] is None


def test_rows_query_quotes_identifiers():
    sql, params = svc.rows_query("db_dev", "mi tabla", ["id", "ñandú"], 51, 100)
    assert sql == "SELECT * FROM `db_dev`.`mi tabla` ORDER BY `id`, `ñandú` LIMIT %s OFFSET %s"
    assert params == (51, 100)
    # Backticks y nulos nunca llegan al SQL (aunque MySQL los permita en un nombre).
    for bad in ("", "ta`bla", "a\x00b", "x" * 65):
        with pytest.raises(ServiceError):
            svc.quote_ident(bad)
    with pytest.raises(NotFoundError):
        svc.check_table_name("x", ["y"])


def test_serialize_value():
    s = svc.serialize_value
    assert s(None) is None and s(5) == 5 and s(2**60) == str(2**60) and s(1.5) == 1.5
    assert s(decimal.Decimal("10.50")) == "10.50"
    assert s(dt.datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    assert s(b"\x00\xff") == "0x00ff (2 bytes)"
    assert s(b"a" * 100).endswith("… (100 bytes)")
    assert len(s("x" * 2000)) < 600 and s("x" * 2000).endswith("(2000 caracteres)")
