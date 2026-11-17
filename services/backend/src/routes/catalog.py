"""Repositorios y listas: el "catálogo" del tablero. Cualquier usuario
autenticado puede administrar repositorios y listas (salvo el color y el orden
de las listas). El borrado es soft delete."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import AdminActor, CurrentActor
from src.models.board_list import BoardList
from src.models.card import Card
from src.models.repository import Repository
from src.schemas.requests import (
    ListBody, ListColorBody, ListReorderBody, ListUpdateBody, RepositoryBody, RepositoryPreviewBody,
)
from src.services import repo_health_service
from src.services.serializers import list_dict, repository_dict

router = APIRouter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_alive(db: AsyncSession, model, obj_id: int, what: str):
    obj = await db.get(model, obj_id)
    if not obj or obj.deleted_at:
        raise HTTPException(404, f"{what} no encontrado")
    return obj


# ── Repositorios ──────────────────────────────────────────────────────────────

async def _ensure_repo_key_free(db: AsyncSession, key: str, exclude_id: int | None = None) -> None:
    query = select(Repository.id).where(Repository.deleted_at.is_(None), func.lower(Repository.key) == key.lower())
    if exclude_id:
        query = query.where(Repository.id != exclude_id)
    if (await db.execute(query)).first():
        raise HTTPException(409, f'Ya existe un repositorio con key "{key}"')


def _apply_repo(repo: Repository, body: RepositoryBody) -> None:
    repo.key = body.key
    repo.name = body.name.strip()
    repo.github_url = (body.githubUrl or "").strip() or None
    repo.default_branch = body.defaultBranch.strip() or "staging"
    repo.local_path = (body.localPath or "").strip() or None
    repo.description = (body.description or "").strip() or None
    repo.color = body.color


@router.get("/repositories")
async def list_repositories(_: CurrentActor, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Repository).where(Repository.deleted_at.is_(None)).order_by(Repository.key))
    return {"success": True, "data": [repository_dict(r) for r in result.scalars()]}


@router.post("/repositories", status_code=201)
async def create_repository(body: RepositoryBody, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    await _ensure_repo_key_free(db, body.key)
    repo = Repository()
    _apply_repo(repo, body)
    db.add(repo)
    await db.commit()
    await repo_health_service.check_new_repository(db, repo)
    return {"success": True, "data": repository_dict(repo), "message": "Repositorio creado"}


@router.put("/repositories/{repo_id}")
async def update_repository(repo_id: int, body: RepositoryBody, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    repo = await _get_alive(db, Repository, repo_id, "Repositorio")
    await _ensure_repo_key_free(db, body.key, exclude_id=repo.id)
    previous_url = repo.github_url
    _apply_repo(repo, body)
    await db.commit()
    if repo.github_url != previous_url:
        await repo_health_service.check_new_repository(db, repo)
    return {"success": True, "data": repository_dict(repo), "message": "Repositorio actualizado"}


@router.put("/repositories/{repo_id}/preview")
async def update_repository_preview(
    repo_id: int, body: RepositoryPreviewBody, _: AdminActor, db: AsyncSession = Depends(get_db)
):
    # Solo admins: la receta se ejecuta en las VMs de pruebas de todos los usuarios.
    repo = await _get_alive(db, Repository, repo_id, "Repositorio")
    repo.preview_script = (body.script or "").strip() or None
    repo.preview_url = (body.url or "").strip() or None
    await db.commit()
    return {"success": True, "data": repository_dict(repo), "message": "Receta de preview actualizada"}


@router.delete("/repositories/{repo_id}")
async def delete_repository(repo_id: int, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    repo = await _get_alive(db, Repository, repo_id, "Repositorio")
    repo.deleted_at = _now()
    await db.commit()
    return {"success": True, "message": "Repositorio eliminado"}


# ── Listas ────────────────────────────────────────────────────────────────────

@router.post("/lists", status_code=201)
async def create_list(body: ListBody, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    max_pos = (await db.execute(
        select(func.max(BoardList.position)).where(BoardList.deleted_at.is_(None))
    )).scalar()
    board_list = BoardList(
        name=body.name.strip(),
        position=(max_pos + 1) if max_pos is not None else 0,
        collapsible=body.collapsible,
    )
    db.add(board_list)
    await db.commit()
    return {"success": True, "data": list_dict(board_list), "message": "Lista creada"}


@router.put("/lists/{list_id}")
async def update_list(list_id: int, body: ListUpdateBody, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    board_list = await _get_alive(db, BoardList, list_id, "Lista")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(422, "El nombre no puede estar vacío")
        board_list.name = body.name.strip()
    if body.collapsible is not None:
        board_list.collapsible = body.collapsible
    await db.commit()
    return {"success": True, "data": list_dict(board_list), "message": "Lista actualizada"}


@router.put("/lists/{list_id}/color")
async def set_list_color(list_id: int, body: ListColorBody, _: AdminActor, db: AsyncSession = Depends(get_db)):
    board_list = await _get_alive(db, BoardList, list_id, "Lista")
    board_list.color = body.color.lower() if body.color else None
    await db.commit()
    return {"success": True, "data": list_dict(board_list), "message": "Color actualizado"}


@router.post("/lists/reorder")
async def reorder_lists(body: ListReorderBody, _: AdminActor, db: AsyncSession = Depends(get_db)):
    for pos, list_id in enumerate(body.ids):
        await db.execute(update(BoardList).where(BoardList.id == list_id).values(position=pos))
    await db.commit()
    return {"success": True, "message": "Listas reordenadas"}


@router.delete("/lists/{list_id}")
async def delete_list(list_id: int, _: CurrentActor, db: AsyncSession = Depends(get_db)):
    board_list = await _get_alive(db, BoardList, list_id, "Lista")
    active = (await db.execute(
        select(func.count()).where(Card.list_id == list_id, Card.archived_at.is_(None))
    )).scalar()
    if active:
        raise HTTPException(409, f"La lista tiene {active} tarjeta(s) activas; movelas o archivalas antes")
    board_list.deleted_at = _now()
    await db.commit()
    return {"success": True, "message": "Lista eliminada"}
