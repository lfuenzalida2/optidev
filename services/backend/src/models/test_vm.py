from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.config.database import Base
from src.models.mixins import SoftDeleteMixin
from src.models.user import User

AUTH_TYPES = ("password", "key")


class TestVm(SoftDeleteMixin, Base):
    """VM de pruebas de un usuario, a la que optidev se conecta por SSH.

    La contraseña o la clave privada se guardan cifradas (`secret_enc`, Fernet;
    ver vm_service) y nunca salen de la API. `host_key` es la clave pública del
    host que se vio en la primera conexión exitosa: las siguientes la exigen,
    para no mandarle las credenciales a otra máquina.
    """

    __test__ = False  # que pytest no la tome por una clase de tests
    __tablename__ = "test_vms"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(100))
    auth_type: Mapped[str] = mapped_column(String(20))
    secret_enc: Mapped[str] = mapped_column(Text)
    host_key: Mapped[str | None] = mapped_column(Text, default=None)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    last_check_message: Mapped[str | None] = mapped_column(String(500), default=None)

    user: Mapped[User] = relationship()
