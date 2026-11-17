from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.database import Base
from src.models.mixins import TimestampMixin
from src.models.repository import Repository
from src.models.test_vm import TestVm
from src.models.user import User

LOAD_STATES = ("running", "success", "failed")


class PreviewLoad(TimestampMixin, Base):
    """Una carga de una rama en la VM de pruebas de un usuario (ver
    preview_service). Queda como historial: la última por (VM, repo) dice
    qué está cargado."""

    __tablename__ = "preview_loads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("test_vms.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    card_id: Mapped[int | None] = mapped_column(Integer, default=None)
    pr_url: Mapped[str | None] = mapped_column(String(255), default=None)
    branch: Mapped[str] = mapped_column(String(255))
    sha: Mapped[str | None] = mapped_column(String(40), default=None)
    status: Mapped[str] = mapped_column(String(20), default="running")
    step: Mapped[str] = mapped_column(String(30), default="fetch")
    log: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship()
    vm: Mapped[TestVm] = relationship()
    repository: Mapped[Repository] = relationship()
