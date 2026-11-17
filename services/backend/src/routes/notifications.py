from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import AdminActor, CurrentActor
from src.schemas.requests import NotificationChannelsBody
from src.services import notification_service

router = APIRouter()


@router.get("")
async def list_notifications(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await notification_service.list_for(db, actor)}


@router.post("/read-all")
async def read_all(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    await notification_service.mark_all_read(db, actor)
    return {"success": True}


@router.get("/channels")
async def get_channels(_: AdminActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await notification_service.get_channels(db)}


@router.put("/channels")
async def set_channels(body: NotificationChannelsBody, _: AdminActor, db: AsyncSession = Depends(get_db)):
    data = await notification_service.set_channels(db, body.model_dump(exclude_none=True))
    return {"success": True, "data": data, "message": "Medios de notificación actualizados"}


@router.post("/{notification_id}/read")
async def read_notification(notification_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    await notification_service.mark_read(db, actor, notification_id)
    return {"success": True}
