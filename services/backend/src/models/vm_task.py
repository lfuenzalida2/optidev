from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.models.mixins import TimestampMixin


class VmTask(TimestampMixin, Base):
    """Tarea de fondo en una VM de pruebas: el arreglo de un problema conocido
    (ver vm_doctor_service) o una skill de Claude sobre un PR (`issue` =
    "skill:<id>", ver skill_service; se puede seguir conversando con Claude en la misma sesión). Deja su log."""

    __tablename__ = "vm_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vm_id: Mapped[int] = mapped_column(ForeignKey("test_vms.id"), index=True)
    issue: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="running")
    log: Mapped[str] = mapped_column(Text, default="")
    # Sesión de Claude de una skill: cada respuesta del usuario la retoma con `--resume` (skill_service.reply).
    session_id: Mapped[str | None] = mapped_column(String(36), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
