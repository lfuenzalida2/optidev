from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base, active_unique_index
from src.models.mixins import SoftDeleteMixin


class Repository(SoftDeleteMixin, Base):
    """Repositorio de la organización. `key` es el nombre corto con el que se
    etiquetan las tarjetas (`helios`, `mms-backend`...) y el que usa Claude
    para saber en qué repo trabajar; `local_path` es dónde está clonado en la
    máquina donde corre Claude (vacío = Claude no tiene acceso)."""

    __tablename__ = "repositories"
    __table_args__ = (active_unique_index("uq_repositories_key", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    github_url: Mapped[str | None] = mapped_column(String(255), default=None)
    default_branch: Mapped[str] = mapped_column(String(100), default="staging")
    local_path: Mapped[str | None] = mapped_column(String(255), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    color: Mapped[str] = mapped_column(String(20), default="#1890ff")
    # Receta para cargar una rama en la VM de pruebas (bash, corre como el
    # usuario SSH de la VM dentro del checkout). La editan solo los admins:
    # se ejecuta en las VMs de todos. Vacía = el repo no se puede cargar.
    preview_script: Mapped[str | None] = mapped_column(Text, default=None)
    # Link para abrir lo cargado; `{host}` se reemplaza por el host de la VM.
    preview_url: Mapped[str | None] = mapped_column(String(255), default=None)
