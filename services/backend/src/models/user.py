from datetime import datetime

import bcrypt
from sqlalchemy import Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base, active_unique_index
from src.models.mixins import SoftDeleteMixin

ROLES = ("admin", "member")


class User(SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        active_unique_index("uq_users_username", "username"),
        active_unique_index("uq_users_email", "email"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Cuándo cerró la guía de bienvenida; NULL = se le muestra al entrar.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Perfil editable por el propio usuario: foto (una imagen de /api/uploads)
    # y color de fondo del avatar con iniciales. NULL = los del tema.
    avatar_url: Mapped[str | None] = mapped_column(String(255), default=None)
    avatar_color: Mapped[str | None] = mapped_column(String(20), default=None)
    # Tema del frontend que eligió el usuario (se guarda acá para que lo siga en cualquier navegador).
    dark_mode: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    # MFA (TOTP). El secreto va cifrado (ver mfa_service). Con secreto y sin
    # `mfa_enabled_at` el registro quedó a medias: se confirma con el primer código.
    mfa_secret: Mapped[str | None] = mapped_column(String(255), default=None)
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Último paso de 30 s aceptado: un código ya usado no sirve de nuevo.
    mfa_last_step: Mapped[int | None] = mapped_column(Integer, default=None)
    mfa_failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    mfa_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Token personal de GitHub del usuario, cifrado (secret_box). Se valida
    # contra GitHub al guardarlo; la API solo devuelve el login.
    github_token_enc: Mapped[str | None] = mapped_column(Text, default=None)
    github_login: Mapped[str | None] = mapped_column(String(100), default=None)
    # Última vez que la rutina diaria de Claude Code (1:00, en la VM del
    # usuario) avisó que corrió, con la tool `report_routine_run` del MCP.
    routine_last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())
