"""¿El usuario está listo para trabajar con optidev? Requisitos mínimos:

- `mfa`: tiene el MFA registrado.
- `vm`: tiene al menos una VM de pruebas cargada en Mi perfil.
- `claudeCode`: Claude Code ya se conectó con uno de sus tokens (un token
  vigente con `last_used_at`; crearlo solo no alcanza).
- `github`: cargó su token personal de GitHub en Mi perfil (se valida al guardarlo).
- `routine`: la rutina diaria de Claude Code corrió al menos una vez (avisa
  con la tool `report_routine_run`; al configurarla se corre una vez a mano).

Se calcula al vuelo, sin guardar nada: para la lista de usuarios se resuelve
con una consulta por requisito, no una por usuario.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.api_token import ApiToken
from src.models.test_vm import TestVm
from src.models.user import User

CHECKS = (
    ("mfa", "MFA registrado"),
    ("vm", "VM de pruebas cargada"),
    ("claudeCode", "Claude Code conectado"),
    ("github", "Token de GitHub cargado"),
    ("routine", "Rutina diaria configurada"),
)


async def readiness_for(db: AsyncSession, users: list[User]) -> dict[int, dict]:
    ids = [u.id for u in users]
    with_vm = set((await db.execute(
        select(TestVm.user_id).where(TestVm.user_id.in_(ids), TestVm.deleted_at.is_(None))
    )).scalars())
    with_claude = set((await db.execute(
        select(ApiToken.user_id).where(
            ApiToken.user_id.in_(ids), ApiToken.revoked_at.is_(None), ApiToken.last_used_at.is_not(None)
        )
    )).scalars())
    result = {}
    for user in users:
        ok = {
            "mfa": user.mfa_enabled_at is not None,
            "vm": user.id in with_vm,
            "claudeCode": user.id in with_claude,
            "github": user.github_token_enc is not None,
            "routine": user.routine_last_run_at is not None,
        }
        checks = [{"key": key, "label": label, "ok": ok[key]} for key, label in CHECKS]
        result[user.id] = {"ready": all(c["ok"] for c in checks), "checks": checks}
    return result


async def readiness(db: AsyncSession, user: User) -> dict:
    return (await readiness_for(db, [user]))[user.id]
