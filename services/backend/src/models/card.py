from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.database import Base
from src.models.board_list import BoardList
from src.models.mixins import TimestampMixin
from src.models.repository import Repository
from src.models.user import User

card_repositories = Table(
    "card_repositories",
    Base.metadata,
    Column("card_id", ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True),
    Column("repository_id", ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True),
)


class Card(TimestampMixin, Base):
    """Las tarjetas no se borran: se archivan (`archived_at`), como en Trello."""

    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("board_lists.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    pr_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    board_list: Mapped[BoardList] = relationship()
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    repositories: Mapped[list[Repository]] = relationship(secondary=card_repositories, order_by=Repository.key)
