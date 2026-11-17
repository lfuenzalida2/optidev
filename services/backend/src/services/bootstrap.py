import logging

from sqlalchemy import func, select

from src.config.database import AsyncSessionLocal
from src.config.settings import settings
from src.models.board_list import BoardList
from src.models.user import User

logger = logging.getLogger(__name__)


async def bootstrap() -> None:
    """Datos mínimos para una instalación nueva: el admin inicial (si no hay
    ningún usuario) y las listas por defecto (si no hay ninguna)."""
    async with AsyncSessionLocal() as db:
        if not (await db.execute(select(func.count()).select_from(User))).scalar():
            if not settings.ADMIN_PASSWORD:
                logger.warning("No hay usuarios y ADMIN_PASSWORD no está definido: no se crea el admin inicial")
            else:
                admin = User(
                    username=settings.ADMIN_USERNAME.lower(),
                    email=settings.ADMIN_EMAIL.lower(),
                    name="Administrador",
                    role="admin",
                )
                admin.set_password(settings.ADMIN_PASSWORD)
                db.add(admin)
                logger.info("Admin inicial '%s' creado", admin.username)

        if not (await db.execute(select(func.count()).select_from(BoardList))).scalar():
            names = [n.strip() for n in settings.DEFAULT_LISTS.split(",") if n.strip()]
            db.add_all(BoardList(name=name, position=pos) for pos, name in enumerate(names))

        await db.commit()
