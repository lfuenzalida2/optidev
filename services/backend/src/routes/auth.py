from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import CurrentActor
from src.models.user import User
from src.schemas.requests import (
    ChangePasswordBody, GithubTokenBody, LoginBody, MfaTokenBody, MfaVerifyBody, ProfileBody,
)
from src.services import board_service, github_service, mfa_service, readiness_service, upload_service
from src.services.auth_service import make_access_token
from src.services.errors import ServiceError
from src.services.secret_box import encrypt_json
from src.services.serializers import user_dict

router = APIRouter()


@router.post("/login")
async def login(body: LoginBody, db: AsyncSession = Depends(get_db)):
    ident = body.username.strip().lower()
    result = await db.execute(
        select(User).where(
            User.deleted_at.is_(None),
            or_(func.lower(User.username) == ident, func.lower(User.email) == ident),
        )
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active or not user.check_password(body.password):
        raise HTTPException(401, "Credenciales incorrectas")
    step = mfa_service.step_for(user)
    if step:
        # Falta el segundo paso: el token de MFA solo sirve para /mfa/setup y /mfa/verify.
        return {"success": True, "data": {"mfa": {"step": step, "token": mfa_service.make_mfa_token(user)}}}
    return {"success": True, "data": {"token": make_access_token(user), "user": user_dict(user)}}


@router.post("/mfa/setup")
async def mfa_setup(body: MfaTokenBody, db: AsyncSession = Depends(get_db)):
    user = await mfa_service.user_from_mfa_token(db, body.mfaToken)
    return {"success": True, "data": await mfa_service.start_setup(db, user)}


@router.post("/mfa/verify")
async def mfa_verify(body: MfaVerifyBody, db: AsyncSession = Depends(get_db)):
    user = await mfa_service.user_from_mfa_token(db, body.mfaToken)
    await mfa_service.verify(db, user, body.code)
    return {"success": True, "data": {"token": make_access_token(user), "user": user_dict(user)}}


@router.get("/me")
async def me(actor: CurrentActor):
    return {"success": True, "data": user_dict(actor.user)}


@router.patch("/me")
async def update_profile(body: ProfileBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, actor.user.id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if not name:
            raise HTTPException(400, "El nombre no puede estar vacío")
        user.name = name
    if "avatarUrl" in changes:
        url = changes["avatarUrl"] or None
        if url:
            # Solo imágenes subidas a optidev: un link externo filtraría quién mira el tablero.
            try:
                if not url.startswith(upload_service.URL_PREFIX):
                    raise ServiceError("no es un upload")
                upload_service.path_for(url.removeprefix(upload_service.URL_PREFIX))
            except ServiceError:
                raise HTTPException(400, "La foto tiene que ser una imagen subida a optidev")
        user.avatar_url = url
    if "avatarColor" in changes:
        user.avatar_color = changes["avatarColor"].lower() if changes["avatarColor"] else None
    if changes.get("darkMode") is not None:
        user.dark_mode = changes["darkMode"]
    await db.commit()
    return {"success": True, "data": user_dict(user), "message": "Perfil actualizado"}


@router.get("/me/readiness")
async def my_readiness(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await readiness_service.readiness(db, actor.user)}


@router.put("/me/github-token")
async def set_github_token(body: GithubTokenBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    token = body.token.strip()
    login = await github_service.fetch_login(token)
    actor.user.github_token_enc = encrypt_json({"token": token})
    actor.user.github_login = login
    await db.commit()
    return {"success": True, "data": user_dict(actor.user), "message": f"Token de GitHub de {login} guardado"}


@router.delete("/me/github-token")
async def delete_github_token(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    actor.user.github_token_enc = None
    actor.user.github_login = None
    await db.commit()
    return {"success": True, "data": user_dict(actor.user), "message": "Token de GitHub quitado"}


@router.get("/me/activity")
async def my_activity(
    actor: CurrentActor,
    limit: int = Query(50, ge=1, le=100),
    before: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    data = await board_service.user_activity(db, actor.user.id, limit, before)
    return {"success": True, "data": data}


@router.post("/change-password")
async def change_password(body: ChangePasswordBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, actor.user.id)
    if not user.check_password(body.currentPassword):
        raise HTTPException(401, "La contraseña actual no es correcta")
    user.set_password(body.newPassword)
    await db.commit()
    return {"success": True, "message": "Contraseña actualizada"}


@router.post("/onboarding/complete")
async def complete_onboarding(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    user = await db.get(User, actor.user.id)
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(timezone.utc)
        await db.commit()
    return {"success": True, "data": user_dict(user)}
