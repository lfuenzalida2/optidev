"""VMs de pruebas de cada usuario: credenciales SSH cifradas y prueba de conexión.

Cada usuario ve y administra solo sus VMs. La contraseña o clave privada se
cifra con Fernet (clave de VM_CREDENTIALS_KEY, o derivada de JWT_SECRET) y
nunca se devuelve: la API solo informa que está cargada.

Clave del host (TOFU): la primera conexión exitosa guarda la clave pública
del servidor y las siguientes solo aceptan esa. Si la VM se recrea (cambia la
clave), el usuario la olvida al editar (`resetHostKey`) o al cambiar host/puerto.
"""

import asyncio
import socket
from datetime import datetime, timezone

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import settings
from src.models.test_vm import AUTH_TYPES, TestVm
from src.models.user import User
from src.services import notification_service, vm_request_service
from src.services.auth_service import Actor
from src.services.errors import NotFoundError, ServiceError
from src.services.secret_box import decrypt_json, encrypt_json
from src.services.serializers import _iso, user_brief


def _encrypt(secret: dict) -> str:
    return encrypt_json(secret)


def _decrypt(token: str) -> dict:
    return decrypt_json(token)


def vm_dict(vm: TestVm) -> dict:
    fingerprint = None
    if vm.host_key:
        fingerprint = asyncssh.import_public_key(vm.host_key).get_fingerprint()
    return {
        "id": vm.id,
        "name": vm.name,
        "host": vm.host,
        "port": vm.port,
        "username": vm.username,
        "authType": vm.auth_type,
        "hostKeyFingerprint": fingerprint,
        "lastCheckAt": _iso(vm.last_check_at),
        "lastCheckOk": vm.last_check_ok,
        "lastCheckMessage": vm.last_check_message,
        "createdAt": _iso(vm.created_at),
        "updatedAt": _iso(vm.updated_at),
    }


def _build_secret(auth_type: str, password: str | None, private_key: str | None, passphrase: str | None) -> dict:
    """Valida y arma el secreto a cifrar. La clave se importa acá para avisar al
    guardar (formato inválido, passphrase incorrecta) y no recién al conectar."""
    if auth_type not in AUTH_TYPES:
        raise ServiceError(f"Tipo de autenticación inválido: {auth_type}")
    if auth_type == "password":
        if not password:
            raise ServiceError("Falta la contraseña")
        return {"password": password}
    if not (private_key or "").strip():
        raise ServiceError("Falta la clave privada")
    try:
        asyncssh.import_private_key(private_key.strip(), passphrase or None)
    except (asyncssh.KeyImportError, ValueError) as exc:
        raise ServiceError(f"La clave privada no es válida o la passphrase es incorrecta: {exc}") from exc
    return {"privateKey": private_key.strip() + "\n", "passphrase": passphrase or None}


def _clean(value: str | None, field: str) -> str:
    value = (value or "").strip()
    if not value or any(c.isspace() for c in value):
        raise ServiceError(f"{field} es obligatorio y no puede tener espacios")
    return value


async def list_vms(db: AsyncSession, actor: Actor) -> list[dict]:
    result = await db.execute(
        select(TestVm).where(TestVm.user_id == actor.user.id, TestVm.deleted_at.is_(None)).order_by(TestVm.id)
    )
    return [vm_dict(vm) for vm in result.scalars()]


async def list_all_vms(db: AsyncSession) -> list[dict]:
    """Las VMs de todos los usuarios, con su dueño (vista de VMs de los admins)."""
    rows = (await db.execute(
        select(TestVm, User).join(User, User.id == TestVm.user_id)
        .where(TestVm.deleted_at.is_(None)).order_by(User.name, TestVm.id)
    )).all()
    return [{**vm_dict(vm), "owner": user_brief(owner)} for vm, owner in rows]


async def get_vm(db: AsyncSession, actor: Actor, vm_id: int, *, admin_any: bool = False) -> TestVm:
    """La VM del actor. Con `admin_any`, un admin accede a la de cualquier usuario: solo para lo que la
    vista de VMs permite (terminal, probar, editar, eliminar). Skills, diagnóstico y previews la dejan en
    False: instalan en la VM un token a nombre del actor, que en una VM ajena sería el del admin."""
    vm = await db.get(TestVm, vm_id)
    allowed = vm and (vm.user_id == actor.user.id or (admin_any and actor.user.role == "admin"))
    # La VM de otro usuario se informa como inexistente: no se revela que existe.
    if not vm or vm.deleted_at or not allowed:
        raise NotFoundError("VM no encontrada")
    return vm


def _new_vm(user_id: int, data: dict) -> TestVm:
    if not data.get("authType"):
        raise ServiceError("Elegí cómo autenticarse (contraseña o clave privada)")
    return TestVm(
        user_id=user_id,
        name=(data.get("name") or "").strip() or "Mi VM",
        host=_clean(data.get("host"), "El host"),
        port=data.get("port") or 22,
        username=_clean(data.get("username"), "El usuario"),
        auth_type=data["authType"],
        secret_enc=_encrypt(_build_secret(data["authType"], data.get("password"), data.get("privateKey"),
                                          data.get("passphrase"))),
    )


async def create_vm(db: AsyncSession, actor: Actor, data: dict, owner_id: int | None = None) -> dict:
    """Crea una VM. Un admin puede crearla para otro usuario (`owner_id`): se le avisa y se cierra su solicitud."""
    owner = actor.user
    if owner_id not in (None, actor.user.id):
        if actor.user.role != "admin":
            raise ServiceError("Solo un admin puede crear la VM de otro usuario", 403)
        owner = await db.get(User, owner_id)
        if not owner or owner.deleted_at or not owner.is_active:
            raise NotFoundError("Usuario no encontrado")
    vm = _new_vm(owner.id, data)
    db.add(vm)
    await vm_request_service.resolve_pending(db, owner.id)
    if owner.id != actor.user.id:
        await notification_service.notify_users(db, [owner.id], "vm_assigned", {"vmName": vm.name})
    await db.commit()
    return vm_dict(vm)


def _apply_changes(vm: TestVm, data: dict) -> None:
    """Aplica una edición parcial sobre `vm` (sin commit). El secreto solo se
    reemplaza si llega uno nuevo (o si cambia el tipo de autenticación, que lo exige)."""
    if data.get("name") is not None:
        vm.name = data["name"].strip() or vm.name
    host = _clean(data["host"], "El host") if data.get("host") is not None else vm.host
    port = data.get("port") or vm.port
    if (host, port) != (vm.host, vm.port) or data.get("resetHostKey"):
        vm.host_key = None
    vm.host, vm.port = host, port
    if data.get("username") is not None:
        vm.username = _clean(data["username"], "El usuario")

    auth_type = data.get("authType") or vm.auth_type
    new_secret = data.get("password") or data.get("privateKey")
    if auth_type != vm.auth_type or new_secret:
        vm.secret_enc = _encrypt(_build_secret(auth_type, data.get("password"), data.get("privateKey"),
                                               data.get("passphrase")))
        vm.auth_type = auth_type


async def update_vm(db: AsyncSession, actor: Actor, vm_id: int, data: dict) -> dict:
    vm = await get_vm(db, actor, vm_id, admin_any=True)
    _apply_changes(vm, data)
    await db.commit()
    await db.refresh(vm)
    return vm_dict(vm)


async def delete_vm(db: AsyncSession, actor: Actor, vm_id: int) -> None:
    vm = await get_vm(db, actor, vm_id, admin_any=True)
    vm.deleted_at = datetime.now(timezone.utc)
    await db.commit()


def _is_local_address(ip: str) -> bool:
    """True si `ip` es de esta máquina: solo se puede hacer bind a IPs locales."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.bind((ip, 0))
        return True
    except OSError:
        return False


def ensure_not_self(vm: TestVm) -> None:
    """Las acciones que modifican la VM (cargar ramas, arreglos) no se hacen
    si la "VM" es el propio servidor de optidev."""
    if settings.PREVIEW_ALLOW_SELF:
        return
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(vm.host, None)}
    except OSError:
        return  # no resuelve: la conexión va a fallar sola con un error claro
    if any(_is_local_address(ip) for ip in addresses):
        raise ServiceError(f"{vm.host} es el servidor de optidev, no una VM de pruebas: cargá en Mi perfil "
                           "la IP de tu VM")


def connect_options(vm: TestVm) -> dict:
    """Argumentos de `asyncssh.connect` para la VM. También los va a usar la
    terminal web. `config=None` y sin agente: no se usa nada del ~/.ssh del
    servidor de optidev, solo las credenciales del usuario."""
    secret = _decrypt(vm.secret_enc)
    options = {
        "host": vm.host,
        "port": vm.port,
        "username": vm.username,
        "config": None,
        "agent_path": None,
        "known_hosts": ([asyncssh.import_public_key(vm.host_key)], [], []) if vm.host_key else None,
        "connect_timeout": settings.VM_SSH_TIMEOUT_SECONDS,
        "login_timeout": settings.VM_SSH_TIMEOUT_SECONDS,
    }
    if vm.auth_type == "password":
        options.update(password=secret["password"], client_keys=None)
    else:
        options.update(client_keys=[asyncssh.import_private_key(secret["privateKey"], secret.get("passphrase"))],
                       password=None)
    return options


def remember_host_key(vm: TestVm, conn: asyncssh.SSHClientConnection) -> bool:
    """Fija la clave del host si todavía no había una (primera conexión). El
    commit queda a cargo de quien llama. Devuelve True si la guardó."""
    if vm.host_key:
        return False
    vm.host_key = conn.get_server_host_key().export_public_key("openssh").decode().strip()
    return True


def connection_error(vm: TestVm, exc: Exception) -> str:
    if isinstance(exc, asyncssh.HostKeyNotVerifiable):
        return ("La clave del host no coincide con la guardada. Si recreaste la VM, editala y marcá "
                "\"Olvidar clave del host\"; si no, alguien podría estar suplantándola.")
    if isinstance(exc, asyncssh.PermissionDenied):
        return "El servidor rechazó el usuario o las credenciales"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return f"Tiempo de espera agotado conectando a {vm.host}:{vm.port}"
    if isinstance(exc, OSError):
        return f"No se pudo conectar a {vm.host}:{vm.port}: {exc.strerror or exc}"
    return f"Error SSH: {exc}"


async def _probe(vm: TestVm) -> tuple[bool, str, str | None]:
    """Se conecta y corre `uname -srm`. Devuelve (ok, mensaje, clave del host
    vista en esta conexión). Los errores de conexión no lanzan: van en el mensaje."""
    try:
        async with asyncssh.connect(**connect_options(vm)) as conn:
            result = await asyncio.wait_for(conn.run("uname -srm", check=False),
                                            settings.VM_SSH_TIMEOUT_SECONDS)
            seen = conn.get_server_host_key().export_public_key("openssh").decode().strip()
            return True, (str(result.stdout or "").strip() or "Conectado"), seen
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        return False, connection_error(vm, exc), None


async def check_connection(db: AsyncSession, actor: Actor, vm_id: int) -> dict:
    """Prueba la VM guardada y registra el resultado. En la primera conexión
    exitosa queda guardada la clave del host."""
    vm = await get_vm(db, actor, vm_id, admin_any=True)
    ok, message, seen = await _probe(vm)
    if ok and not vm.host_key:
        vm.host_key = seen
    vm.last_check_at = datetime.now(timezone.utc)
    vm.last_check_ok = ok
    vm.last_check_message = message[:500]
    await db.commit()
    await db.refresh(vm)
    return vm_dict(vm)


async def test_draft(db: AsyncSession, actor: Actor, data: dict, vm_id: int | None = None) -> dict:
    """Prueba los datos del formulario sin guardar nada (no bloquea el alta ni
    la edición). Con `vm_id` parte de la VM guardada: los secretos vacíos usan
    el guardado y, si no cambian host/puerto, se exige la clave de host fijada."""
    if vm_id is None:
        draft = _new_vm(actor.user.id, data)
    else:
        saved = await get_vm(db, actor, vm_id, admin_any=True)
        draft = TestVm(**{col: getattr(saved, col) for col in (
            "user_id", "name", "host", "port", "username", "auth_type", "secret_enc", "host_key")})
        _apply_changes(draft, data)
    ok, message, seen = await _probe(draft)
    fingerprint = asyncssh.import_public_key(seen).get_fingerprint() if seen else None
    return {"ok": ok, "message": message, "hostKeyFingerprint": fingerprint}
