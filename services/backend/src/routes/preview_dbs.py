"""Visualizador de las bases de preview (`db_<usuario>`). Ver preview_db_service."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.schemas.requests import PreviewDbResetBody
from src.services import preview_db_service

router = APIRouter()


@router.get("")
async def list_databases(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """Las bases de preview que ve el usuario: la suya, o todas si es admin."""
    return {"success": True, "data": await preview_db_service.list_databases(db, actor)}


@router.get("/{username}/tables")
async def list_tables(username: str, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await preview_db_service.list_tables(db, actor, username)}


@router.get("/{username}/columns")
async def table_detail(username: str, actor: CurrentActor, table: str = Query(max_length=64),
                       db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await preview_db_service.table_detail(db, actor, username, table)}


@router.get("/{username}/rows")
async def table_rows(username: str, actor: CurrentActor, table: str = Query(max_length=64),
                     page: int = Query(1, ge=1),
                     pageSize: int = Query(50, ge=1, le=preview_db_service.MAX_PAGE_SIZE),  # noqa: N803
                     db: AsyncSession = Depends(get_db)):
    data = await preview_db_service.table_rows(db, actor, username, table, page, pageSize)
    return {"success": True, "data": data}


@router.post("/{username}/prepare")
async def prepare(username: str, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await preview_db_service.prepare(db, actor, username)
    return {"success": True, "data": data, "message": f"Base {data['database']} lista"}


@router.post("/{username}/reset")
async def reset(username: str, body: PreviewDbResetBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await preview_db_service.reset(db, actor, username, body.confirm)
    return {"success": True, "data": data, "message": f"Base {data['database']} recreada vacía"}
