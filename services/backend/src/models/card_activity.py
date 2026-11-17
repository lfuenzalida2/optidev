from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.database import Base
from src.models.mixins import utcnow
from src.models.user import User

ACTIONS = ("created", "updated", "moved", "archived", "unarchived", "commented", "pr_added")


class CardActivity(Base):
    """Historial append-only de una tarjeta. `data` guarda el detalle del
    cambio (lista origen/destino, campos tocados, link del PR...)."""

    __tablename__ = "card_activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    via: Mapped[str] = mapped_column(String(10), default="web")
    action: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    actor: Mapped[User | None] = relationship()
