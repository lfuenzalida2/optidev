from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.services.auth_service import Actor, resolve_bearer

security = HTTPBearer(auto_error=False)


async def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Actor:
    if not credentials:
        raise HTTPException(401, "No autenticado")
    resolved = await resolve_bearer(db, credentials.credentials)
    if not resolved:
        raise HTTPException(401, "Token inválido o expirado")
    user, via = resolved
    return Actor(user=user, via=via)


async def require_admin(actor: Annotated[Actor, Depends(get_current_actor)]) -> Actor:
    if actor.user.role != "admin":
        raise HTTPException(403, "Requiere rol admin")
    return actor


CurrentActor = Annotated[Actor, Depends(get_current_actor)]
AdminActor = Annotated[Actor, Depends(require_admin)]
