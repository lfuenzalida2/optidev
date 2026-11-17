from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import AdminActor, CurrentActor
from src.schemas.requests import PreviewMysqlBody, PreviewVaultBody
from src.services import preview_env_service

router = APIRouter()


@router.get("")
async def status(_: AdminActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await preview_env_service.status(db)}


@router.post("/vault")
async def connect_vault(body: PreviewVaultBody, _: AdminActor, db: AsyncSession = Depends(get_db)):
    """Configura el Vault con un token de admin (no se guarda) y guarda la AppRole de optidev."""
    data = await preview_env_service.connect(db, body.addr, body.token)
    return {"success": True, "data": data, "message": "Vault conectado"}


@router.put("/mysql")
async def set_mysql(body: PreviewMysqlBody, _: AdminActor, db: AsyncSession = Depends(get_db)):
    data = await preview_env_service.set_mysql(db, body.host, body.port, body.adminUser, body.adminPassword)
    return {"success": True, "data": data, "message": "MySQL central guardado"}


@router.post("/me")
async def prepare_mine(actor: CurrentActor):
    """Deja listo el ambiente del usuario (mount, AppRole, base de MySQL). Las
    cargas lo hacen solas; esto sirve para probarlo sin cargar una rama."""
    context = await preview_env_service.ensure_user(actor.user.username)
    return {"success": True, "data": {"mount": context["VAULT_MOUNT_POINT"], "database": context["DB_NAME"],
                                      "dbUser": context["DB_USERNAME"], "dbHost": context["DB_HOST"]},
            "message": "Ambiente listo"}
