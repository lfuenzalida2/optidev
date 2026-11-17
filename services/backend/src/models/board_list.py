from sqlalchemy import Boolean, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.models.mixins import SoftDeleteMixin


class BoardList(SoftDeleteMixin, Base):
    __tablename__ = "board_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Lista "ocultable" (p. ej. Backlog): en la web se muestra colapsada por
    # defecto y cada usuario puede expandirla. Es solo visual: la API y el MCP
    # devuelven sus tarjetas igual que las de cualquier otra lista.
    collapsible: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Color del encabezado en el tablero (lo configura un admin); NULL = el neutro del tema.
    color: Mapped[str | None] = mapped_column(String(20), default=None)
