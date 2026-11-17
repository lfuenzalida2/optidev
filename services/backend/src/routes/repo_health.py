"""Salud de los repositorios (bloqueo de ramas, labels, privacidad, Actions). Ver repo_health_service."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import AdminActor, CurrentActor
from src.schemas.requests import RepoFixBody
from src.services import repo_fix_service, repo_health_service

router = APIRouter()


@router.get("")
async def repo_health(_: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await repo_health_service.report(db)}


@router.post("/refresh")
async def refresh(_: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await repo_health_service.report(db, force=True)}


@router.post("/{key}/fix")
async def fix(key: str, body: RepoFixBody, actor: AdminActor, db: AsyncSession = Depends(get_db)):
    """Arregla lo que falla de un repo con un token de GitHub temporal (solo admins; no se guarda). Ver repo_fix_service."""
    data = await repo_fix_service.fix_repository(db, actor, key, body.token.get_secret_value(), body.checks)
    fixed = sum(1 for r in data["results"].values() if r["status"] == "fixed")
    return {"success": True, "data": data, "message": f"{fixed} arreglo(s) aplicado(s)"}


@router.post("/{key}/card", status_code=201)
async def pending_card(key: str, response: Response, actor: AdminActor, db: AsyncSession = Depends(get_db)):
    """Tarjeta en Backlog con lo que el repo no cumple (solo admins). Si ya hay una, devuelve esa (200)."""
    data = await repo_fix_service.create_pending_card(db, actor, key)
    if not data["created"]:
        response.status_code = 200
    return {"success": True, "data": data,
            "message": "Tarjeta creada en Backlog" if data["created"] else "Ya había una tarjeta con lo pendiente"}
