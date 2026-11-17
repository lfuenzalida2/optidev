from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.database import Base
from src.models.mixins import SoftDeleteMixin
from src.models.user import User


class CardComment(SoftDeleteMixin, Base):
    __tablename__ = "card_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    # Origen del comentario: "web" (la app), "api" (token REST) o "mcp" (Claude Code).
    via: Mapped[str] = mapped_column(String(10), default="web")

    author: Mapped[User] = relationship()
