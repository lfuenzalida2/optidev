"""Solicitud de una VM de pruebas: un usuario la pide y avisa a los admins, que se la
crean y asignan (al crearle una VM, la solicitud se cierra). Una sola abierta por usuario (ver el modelo VmRequest)."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User
from src.models.vm_request import VmRequest
from src.services import notification_service
from src.services.auth_service import Actor
from src.services.errors import ConflictError, ServiceError
from src.services.serializers import user_brief, vm_request_dict


async def _pending(db: AsyncSession, actor: Actor) -> VmRequest | None:
    return (await db.execute(
        select(VmRequest).where(VmRequest.pending_user_id == actor.user.id)
    )).scalar_one_or_none()


async def get_pending(db: AsyncSession, actor: Actor) -> dict | None:
    request = await _pending(db, actor)
    return vm_request_dict(request) if request else None


async def list_pending(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(
        select(VmRequest, User).join(User, User.id == VmRequest.user_id)
        .where(VmRequest.pending_user_id.is_not(None)).order_by(VmRequest.id)
    )).all()
    return [{**vm_request_dict(request), "user": user_brief(user)} for request, user in rows]


async def resolve_pending(db: AsyncSession, user_id: int) -> None:
    """Cierra la solicitud abierta del usuario (sin commit: lo hace quien le crea la VM)."""
    request = (await db.execute(select(VmRequest).where(VmRequest.pending_user_id == user_id))).scalar_one_or_none()
    if request:
        request.pending_user_id = None
        request.resolved_at = datetime.now(timezone.utc)


async def request_vm(db: AsyncSession, actor: Actor) -> dict:
    user = actor.user
    if user.role == "admin":
        raise ServiceError("Los admins crean sus propias VMs")
    if await _pending(db, actor):
        raise ConflictError("Ya tenés una solicitud de VM abierta")
    request = VmRequest(user_id=user.id, pending_user_id=user.id)
    db.add(request)
    await notification_service.notify_admins(db, "vm_requested", {"userId": user.id, "username": user.username, "name": user.name})
    try:
        await db.commit()
    except IntegrityError:
        # Dos pedidos a la vez: el único de la base dejó pasar solo uno.
        await db.rollback()
        raise ConflictError("Ya tenés una solicitud de VM abierta")
    return vm_request_dict(request)
