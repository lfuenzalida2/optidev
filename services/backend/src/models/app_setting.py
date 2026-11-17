from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base
from src.models.mixins import TimestampMixin


class AppSetting(TimestampMixin, Base):
    """Configuración del sistema que se edita desde la app (no desde el .env).
    El valor va cifrado (secret_box): puede tener credenciales."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_enc: Mapped[str] = mapped_column(Text)
