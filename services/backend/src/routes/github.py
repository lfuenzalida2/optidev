from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.schemas.requests import CardFromPullBody
from src.services import board_service, testing_service

router = APIRouter()


@router.get("/pulls")
async def open_pull_requests(_: CurrentActor, db: AsyncSession = Depends(get_db), refresh: bool = False):
    return {"success": True, "data": await board_service.open_pulls_with_cards(db, force=refresh)}


@router.post("/pulls/card", status_code=201)
async def card_from_pull_request(body: CardFromPullBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.create_card_from_pull_request(db, actor, body.repoKey, body.number)
    return {"success": True, "data": data, "message": "Tarjeta creada en PR"}


@router.get("/testing")
async def testing_queue(
    _: CurrentActor,
    db: AsyncSession = Depends(get_db),
    listIds: Annotated[list[int] | None, Query()] = None,
    refresh: bool = False,
):
    return {"success": True, "data": await testing_service.testing_queue(db, listIds, force=refresh)}
