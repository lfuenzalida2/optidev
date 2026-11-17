from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.schemas.requests import WorkflowChatBody, WorkflowPullRequestBody
from src.services import workflow_service

router = APIRouter()


@router.get("/tree")
async def tree(_: CurrentActor, db: AsyncSession = Depends(get_db), refresh: bool = False):
    return {"success": True, "data": await workflow_service.tree(db, force=refresh)}


@router.get("/file")
async def read_file(
    _: CurrentActor,
    db: AsyncSession = Depends(get_db),
    path: str = Query(min_length=1),
    ref: str | None = Query(default=None, pattern=r"^[0-9a-f]{40}$"),
):
    return {"success": True, "data": await workflow_service.read_file(db, path, ref)}


@router.post("/chat")
async def chat(body: WorkflowChatBody, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    messages = [m.model_dump() for m in body.messages]
    return {"success": True, "data": await workflow_service.chat(db, messages)}


@router.post("/pull-requests", status_code=201)
async def create_pull_request(body: WorkflowPullRequestBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await workflow_service.create_pull_request(
        db, actor, body.title, body.description, body.baseSha, [f.model_dump() for f in body.files],
    )
    return {"success": True, "data": data, "message": "PR abierto"}
