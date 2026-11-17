"""Utilidades sueltas para el equipo (no tocan la BD)."""

import hashlib

from fastapi import APIRouter

from src.middleware.auth import CurrentActor
from src.schemas.requests import Md5Body

router = APIRouter()


@router.post("/md5")
async def md5(body: Md5Body, _: CurrentActor):
    digest = hashlib.md5(body.text.encode("utf-8")).hexdigest()
    return {"success": True, "data": {"md5": digest}}
