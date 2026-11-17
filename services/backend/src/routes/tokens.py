from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.models.api_token import ApiToken
from src.schemas.requests import ApiTokenCreateBody
from src.services.auth_service import generate_api_token, hash_api_token
from src.services.serializers import api_token_dict

router = APIRouter()


@router.get("")
async def list_tokens(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == actor.user.id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return {"success": True, "data": [api_token_dict(t) for t in result.scalars()]}


@router.post("", status_code=201)
async def create_token(body: ApiTokenCreateBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    raw = generate_api_token()
    token = ApiToken(user_id=actor.user.id, name=body.name.strip(), token_hash=hash_api_token(raw), prefix=raw[:10])
    db.add(token)
    await db.commit()
    # Única vez que se devuelve el valor en claro.
    return {"success": True, "data": {**api_token_dict(token), "token": raw}}


@router.delete("/{token_id}")
async def revoke_token(token_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    token = await db.get(ApiToken, token_id)
    if not token or token.user_id != actor.user.id or token.revoked_at:
        raise HTTPException(404, "Token no encontrado")
    token.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "message": "Token revocado"}
