from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.models.mixins import TimestampMixin


class VmRequest(TimestampMixin, Base):
    """Solicitud de una VM de pruebas que un usuario le hace a los admins.

    `pending_user_id` vale el `user_id` mientras la solicitud está abierta y NULL al
    resolverla: el único sobre esa columna impide dos abiertas del mismo usuario (los
    NULL no chocan en un único, ni en SQLite ni en MySQL), sin índices parciales.
    """

    __tablename__ = "vm_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    pending_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), unique=True, default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
