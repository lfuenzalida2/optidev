from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.models.mixins import TimestampMixin


class Notification(TimestampMixin, Base):
    """Aviso para un usuario (una fila por destinatario, así cada uno lo marca leído por su cuenta).
    El texto lo arma el frontend según `kind`; `data` trae lo que ese tipo necesita."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
