"""Vault en memoria para los tests: implementa solo las rutas que usa optidev
(AppRole, KV v2, mounts, políticas) y **aplica las políticas ACL** (globs `*`
al final y `+` por segmento), así un permiso que falte en la política de
optidev hace fallar los tests igual que el Vault real."""

import json
import re
import secrets

import httpx2

ROOT_TOKEN = "root-token-de-prueba"
_RULE = re.compile(r'path\s+"([^"]+)"\s*\{\s*capabilities\s*=\s*\[([^\]]*)\]', re.S)


def _matches(pattern: str, path: str) -> bool:
    regex = ""
    for i, char in enumerate(pattern):
        if char == "*" and i == len(pattern) - 1:
            regex += ".*"
        elif char == "+":
            regex += "[^/]+"
        else:
            regex += re.escape(char)
    return re.fullmatch(regex, path) is not None


class FakeVault:
    def __init__(self):
        self.tokens = {ROOT_TOKEN: ["root"]}
        self.mounts = {"secret": "kv", "cubbyhole": "cubbyhole", "sys": "system"}
        self.auth = {"token"}
        self.policies: dict[str, str] = {}
        self.roles: dict[str, dict] = {}
        self.kv: dict[tuple[str, str], dict] = {}
        self.requests: list[tuple[str, str]] = []

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def _allowed(self, policies: list[str], path: str, capability: str) -> bool:
        if "root" in policies:
            return True
        for name in policies:
            for pattern, caps in _RULE.findall(self.policies.get(name, "")):
                if _matches(pattern, path) and capability in re.findall(r'"(\w+)"', caps):
                    return True
        return False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/v1/")
        method = request.method
        body = json.loads(request.content) if request.content else {}
        self.requests.append((method, path))

        def ok(data=None, status=200):
            return httpx2.Response(status, json=data) if data is not None else httpx2.Response(204)

        def error(status, message):
            return httpx2.Response(status, json={"errors": [message]})

        if path == "auth/approle/login" and method == "POST":
            for role in self.roles.values():
                if role["role_id"] == body.get("role_id") and body.get("secret_id") in role["secret_ids"]:
                    token = "s." + secrets.token_hex(8)
                    self.tokens[token] = list(role["params"].get("token_policies") or [])
                    return ok({"auth": {"client_token": token}})
            return error(400, "invalid role or secret ID")

        policies = self.tokens.get(request.headers.get("X-Vault-Token"))
        if policies is None:
            return error(403, "permission denied")
        capability = "read" if method == "GET" else "update"
        if method in ("POST", "PUT") and not self._exists(path):
            capability = "create"
        if not self._allowed(policies, path, capability):
            return error(403, "permission denied")

        if path == "sys/mounts" and method == "GET":
            return ok({"data": {f"{m}/": {"type": t} for m, t in self.mounts.items()}})
        if path.startswith("sys/mounts/") and method == "POST":
            mount = path.removeprefix("sys/mounts/")
            if mount in self.mounts:
                return error(400, f"path is already in use at {mount}/")
            self.mounts[mount] = body["type"]
            return ok()
        if path == "sys/auth" and method == "GET":
            return ok({"data": {f"{a}/": {"type": a} for a in self.auth}})
        if path == "sys/auth/approle" and method == "POST":
            self.auth.add("approle")
            return ok()
        if path.startswith("sys/policies/acl/") and method == "PUT":
            self.policies[path.removeprefix("sys/policies/acl/")] = body["policy"]
            return ok()

        role_match = re.fullmatch(r"auth/approle/role/([^/]+)(?:/(role-id|secret-id))?", path)
        if role_match:
            if "approle" not in self.auth:
                return error(404, "no handler for route")
            name, action = role_match.groups()
            if action is None and method == "POST":
                role = self.roles.setdefault(name, {"role_id": secrets.token_hex(8), "secret_ids": set()})
                role["params"] = body
                return ok()
            role = self.roles.get(name)
            if not role:
                return error(404, "role not found")
            if action == "role-id":
                return ok({"data": {"role_id": role["role_id"]}})
            if action == "secret-id" and method == "POST":
                secret_id = secrets.token_hex(12)
                role["secret_ids"].add(secret_id)
                return ok({"data": {"secret_id": secret_id}})

        kv_match = re.fullmatch(r"([^/]+)/data/(.+)", path)
        if kv_match and self.mounts.get(kv_match.group(1)) == "kv":
            key = kv_match.groups()
            if method == "GET":
                return ok({"data": {"data": self.kv[key]}}) if key in self.kv else error(404, "")
            self.kv[key] = dict(body["data"])
            return ok({"data": {"version": 1}})
        return error(404, f"no handler for {method} {path}")

    def _exists(self, path: str) -> bool:
        kv_match = re.fullmatch(r"([^/]+)/data/(.+)", path)
        if kv_match:
            return kv_match.groups() in self.kv
        role_match = re.fullmatch(r"auth/approle/role/([^/]+)", path)
        if role_match:
            return role_match.group(1) in self.roles
        if path.startswith("sys/policies/acl/"):
            return path.removeprefix("sys/policies/acl/") in self.policies
        return False
