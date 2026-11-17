from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.schemas.requests import PreviewLoadBody
from src.services import preview_service

router = APIRouter()


@router.get("")
async def current_loads(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """Lo último cargado en cada VM del usuario, por repo."""
    return {"success": True, "data": await preview_service.current_loads(db, actor)}


@router.post("", status_code=202)
async def start_load(body: PreviewLoadBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await preview_service.start_load(db, actor, body.model_dump())
    return {"success": True, "data": data, "message": "Carga iniciada"}


@router.get("/{load_id}")
async def get_load(load_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await preview_service.get_load(db, actor, load_id)}
