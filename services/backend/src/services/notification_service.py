"""Notificaciones dentro de optidev. Cada medio (canal) se puede activar o desactivar
desde Configuración; hoy el único es `inApp` (la campana del header). Para sumar otro
(correo, Slack...): agregarlo a CHANNELS y mandar por él en `notify_users`.

Los avisos se crean en la sesión de quien llama: el commit lo hace él, así el hecho
que se avisa y su notificación se guardan juntos."""

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.app_setting import AppSetting
from src.models.notification import Notification
from src.models.user import User
from src.services.auth_service import Actor
from src.services.errors import NotFoundError, ServiceError
from src.services.secret_box import decrypt_json, encrypt_json
from src.services.serializers import notification_dict

CHANNELS = ("inApp",)
SETTING_KEY = "notification_channels"
LIST_LIMIT = 50


async def get_channels(db: AsyncSession) -> dict[str, bool]:
    row = await db.get(AppSetting, SETTING_KEY)
    saved = decrypt_json(row.value_enc) if row else {}
    return {channel: bool(saved.get(channel, True)) for channel in CHANNELS}


async def set_channels(db: AsyncSession, changes: dict[str, bool]) -> dict[str, bool]:
    unknown = set(changes) - set(CHANNELS)
    if unknown:
        raise ServiceError(f"Medios inexistentes: {', '.join(sorted(unknown))}. Disponibles: {', '.join(CHANNELS)}")
    channels = {**await get_channels(db), **changes}
    row = await db.get(AppSetting, SETTING_KEY)
    if row:
        row.value_enc = encrypt_json(channels)
    else:
        db.add(AppSetting(key=SETTING_KEY, value_enc=encrypt_json(channels)))
    await db.commit()
    return channels


async def notify_users(db: AsyncSession, user_ids: list[int], kind: str, data: dict) -> None:
    if (await get_channels(db))["inApp"]:
        db.add_all(Notification(user_id=user_id, kind=kind, data=data) for user_id in user_ids)


async def notify_admins(db: AsyncSession, kind: str, data: dict) -> None:
    admins = (await db.execute(
        select(User.id).where(User.role == "admin", User.is_active.is_(True), User.deleted_at.is_(None))
    )).scalars().all()
    await notify_users(db, admins, kind, data)


async def list_for(db: AsyncSession, actor: Actor) -> dict:
    items = (await db.execute(
        select(Notification).where(Notification.user_id == actor.user.id)
        .order_by(Notification.id.desc()).limit(LIST_LIMIT)
    )).scalars()
    unread = (await db.execute(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == actor.user.id, Notification.read_at.is_(None))
    )).scalar_one()
    return {"items": [notification_dict(n) for n in items], "unreadCount": unread}


async def mark_read(db: AsyncSession, actor: Actor, notification_id: int) -> None:
    notification = await db.get(Notification, notification_id)
    # La de otro usuario se informa como inexistente, igual que las VMs.
    if not notification or notification.user_id != actor.user.id:
        raise NotFoundError("Notificación no encontrada")
    if not notification.read_at:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()


async def mark_all_read(db: AsyncSession, actor: Actor) -> None:
    await db.execute(
        update(Notification)
        .where(Notification.user_id == actor.user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    await db.commit()
