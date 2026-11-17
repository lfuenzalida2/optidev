from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.schemas.requests import CardCreateBody, CardMoveBody, CardUpdateBody, CommentBody, QaReviewBody
from src.services import board_service, merge_service

router = APIRouter()


@router.get("/board")
async def get_board(_: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await board_service.get_board(db)}


@router.get("/cards")
async def search_cards(
    _: CurrentActor,
    db: AsyncSession = Depends(get_db),
    listId: int | None = None,
    repository: str | None = None,
    q: str | None = None,
    archived: bool = False,
):
    data = await board_service.search_cards(
        db, list_ref=listId, repository=repository, text=q, archived=archived
    )
    return {"success": True, "data": data}


@router.post("/cards", status_code=201)
async def create_card(body: CardCreateBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.create_card(
        db, actor,
        title=body.title, list_ref=body.listId, description=body.description,
        repositories=body.repositories, assignee=body.assigneeId, position=body.position,
    )
    return {"success": True, "data": data, "message": "Tarjeta creada"}


@router.get("/cards/{card_id}")
async def get_card(card_id: int, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await board_service.get_card_detail(db, card_id)}


@router.patch("/cards/{card_id}")
async def update_card(card_id: int, body: CardUpdateBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    if "assigneeId" in changes:
        changes["assignee"] = changes.pop("assigneeId")
    data = await board_service.update_card(db, actor, card_id, changes)
    return {"success": True, "data": data}


@router.post("/cards/{card_id}/move")
async def move_card(card_id: int, body: CardMoveBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.move_card(db, actor, card_id, body.listId, body.position)
    return {"success": True, "data": data}


@router.post("/cards/{card_id}/qa")
async def qa_review(card_id: int, body: QaReviewBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.qa_review(db, actor, card_id, body.approved, body.notes, body.listId)
    return {"success": True, "data": data, "message": "QA aprobado" if body.approved else "QA rechazado"}


@router.post("/cards/{card_id}/rebase")
async def rebase_pull_requests(card_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """Rebasea sobre su rama base los PRs abiertos de la tarjeta, sin mergearlos (resultado por PR)."""
    data = await merge_service.rebase_card(db, actor, card_id)
    rebased = sum(1 for r in data["results"] if r["status"] == "rebased")
    return {"success": True, "data": data, "message": f"{rebased} de {len(data['results'])} PR(s) rebaseado(s)"}


@router.post("/cards/{card_id}/merge")
async def merge_pull_requests(card_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """Rebasea sobre su rama base y mergea los PRs "Aprobado" de la tarjeta (resultado por PR)."""
    data = await merge_service.merge_card(db, actor, card_id)
    merged = sum(1 for r in data["results"] if r["status"] == "merged")
    return {"success": True, "data": data, "message": f"{merged} de {len(data['results'])} PR(s) mergeado(s)"}


@router.post("/cards/{card_id}/archive")
async def archive_card(card_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.set_archived(db, actor, card_id, True)
    return {"success": True, "data": data, "message": "Tarjeta archivada"}


@router.post("/cards/{card_id}/unarchive")
async def unarchive_card(card_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.set_archived(db, actor, card_id, False)
    return {"success": True, "data": data, "message": "Tarjeta restaurada"}


@router.post("/cards/{card_id}/comments", status_code=201)
async def add_comment(card_id: int, body: CommentBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await board_service.add_comment(db, actor, card_id, body.body)
    return {"success": True, "data": data}


@router.delete("/cards/{card_id}/comments/{comment_id}")
async def delete_comment(card_id: int, comment_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    await board_service.delete_comment(db, actor, card_id, comment_id)
    return {"success": True, "message": "Comentario eliminado"}
