"""Cliente mínimo de la API HTTP de Vault: solo lo que usa el ambiente de
preview (KV v2, mounts, políticas y AppRole). `transport` se reemplaza en los
tests por un Vault en memoria."""

import json

import httpx2

from src.services.errors import ServiceError

# Lo pisan los tests (tests/fake_vault.py).
transport = None


class VaultError(ServiceError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message, 502)
        self.status = status


class VaultClient:
    def __init__(self, addr: str, token: str | None = None):
        self.addr = addr.rstrip("/")
        self.token = token

    async def request(self, method: str, path: str, body: dict | None = None,
                      missing_ok: bool = False) -> dict | None:
        headers = {"X-Vault-Token": self.token} if self.token else {}
        try:
            async with httpx2.AsyncClient(timeout=15, transport=transport) as client:
                resp = await client.request(method, f"{self.addr}/v1/{path}", json=body, headers=headers)
        except httpx2.HTTPError as exc:
            raise VaultError(f"No se pudo conectar al Vault ({self.addr}): {exc}") from exc
        if resp.status_code == 404 and missing_ok:
            return None
        if resp.status_code >= 400:
            try:
                errors = "; ".join(resp.json().get("errors") or [])
            except ValueError:
                errors = resp.text[:200]
            if resp.status_code == 403:
                errors = f"permiso denegado ({errors or 'el token no alcanza'})"
            raise VaultError(f"Vault respondió {resp.status_code} en {path}: {errors}", resp.status_code)
        return resp.json() if resp.content else None

    # --- Autenticación --------------------------------------------------------

    async def login_approle(self, role_id: str, secret_id: str) -> str:
        data = await self.request("POST", "auth/approle/login", {"role_id": role_id, "secret_id": secret_id})
        self.token = data["auth"]["client_token"]
        return self.token

    # --- KV v2 ------------------------------------------------------------------

    async def kv_read(self, mount: str, path: str) -> dict | None:
        data = await self.request("GET", f"{mount}/data/{path}", missing_ok=True)
        return data["data"]["data"] if data else None

    async def kv_write(self, mount: str, path: str, values: dict) -> None:
        await self.request("POST", f"{mount}/data/{path}", {"data": values})

    # --- Configuración (solo con un token que lo permita) ----------------------

    async def mounts(self) -> set[str]:
        data = await self.request("GET", "sys/mounts")
        return {name.rstrip("/") for name in (data.get("data") or data) if name.endswith("/")}

    async def ensure_kv2(self, mount: str) -> bool:
        """Habilita un KV v2 en `mount` si no existe. True si lo creó."""
        if mount in await self.mounts():
            return False
        await self.request("POST", f"sys/mounts/{mount}", {"type": "kv", "options": {"version": "2"}})
        return True

    async def ensure_approle(self) -> bool:
        data = await self.request("GET", "sys/auth")
        if "approle/" in (data.get("data") or data):
            return False
        await self.request("POST", "sys/auth/approle", {"type": "approle"})
        return True

    async def write_policy(self, name: str, hcl: str) -> None:
        await self.request("PUT", f"sys/policies/acl/{name}", {"policy": hcl})

    async def write_role(self, name: str, **params) -> None:
        await self.request("POST", f"auth/approle/role/{name}", params)

    async def role_id(self, name: str) -> str:
        return (await self.request("GET", f"auth/approle/role/{name}/role-id"))["data"]["role_id"]

    async def new_secret_id(self, name: str, metadata: dict | None = None) -> str:
        body = {"metadata": json.dumps(metadata)} if metadata else {}
        return (await self.request("POST", f"auth/approle/role/{name}/secret-id", body))["data"]["secret_id"]
