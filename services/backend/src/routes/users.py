from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import AdminActor
from src.models.user import ROLES, User
from src.schemas.requests import UserCreateBody, UserUpdateBody
from src.services import mfa_service, readiness_service
from src.services.serializers import user_dict

router = APIRouter()


async def _get_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(404, "Usuario no encontrado")
    return user


@router.get("")
async def list_users(_: AdminActor, db: AsyncSession = Depends(get_db)):
    users = list((await db.execute(select(User).where(User.deleted_at.is_(None)).order_by(User.name))).scalars())
    readiness = await readiness_service.readiness_for(db, users)
    return {"success": True, "data": [{**user_dict(u), "readiness": readiness[u.id]} for u in users]}


@router.post("", status_code=201)
async def create_user(body: UserCreateBody, _: AdminActor, db: AsyncSession = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(400, f"Rol inválido, debe ser uno de: {', '.join(ROLES)}")
    username, email = body.username.strip().lower(), body.email.strip().lower()
    existing = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            or_(func.lower(User.username) == username, func.lower(User.email) == email),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "El username o email ya están en uso")
    user = User(username=username, email=email, name=body.name.strip(), role=body.role)
    user.set_password(body.password)
    db.add(user)
    await db.commit()
    return {"success": True, "data": user_dict(user), "message": "Usuario creado"}


@router.patch("/{user_id}")
async def update_user(user_id: int, body: UserUpdateBody, actor: AdminActor, db: AsyncSession = Depends(get_db)):
    user = await _get_user(db, user_id)
    if body.role is not None:
        if body.role not in ROLES:
            raise HTTPException(400, f"Rol inválido, debe ser uno de: {', '.join(ROLES)}")
        if user.id == actor.user.id and body.role != "admin":
            raise HTTPException(400, "No podés quitarte el rol admin a vos mismo")
        user.role = body.role
    if body.isActive is not None:
        if user.id == actor.user.id and not body.isActive:
            raise HTTPException(400, "No podés desactivar tu propio usuario")
        user.is_active = body.isActive
    if body.email is not None:
        user.email = body.email.strip().lower()
    if body.name is not None:
        user.name = body.name.strip()
    if body.password:
        user.set_password(body.password)
    await db.commit()
    return {"success": True, "data": user_dict(user), "message": "Usuario actualizado"}


@router.delete("/{user_id}")
async def delete_user(user_id: int, actor: AdminActor, db: AsyncSession = Depends(get_db)):
    user = await _get_user(db, user_id)
    if user.id == actor.user.id:
        raise HTTPException(400, "No podés borrar tu propio usuario")
    user.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "message": "Usuario eliminado"}


@router.post("/{user_id}/mfa/reset")
async def reset_mfa(user_id: int, _: AdminActor, db: AsyncSession = Depends(get_db)):
    """Borra el MFA del usuario: lo vuelve a registrar en su próximo ingreso."""
    user = await _get_user(db, user_id)
    await mfa_service.reset(db, user)
    return {"success": True, "data": user_dict(user), "message": "MFA reiniciado"}
