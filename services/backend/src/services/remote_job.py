"""Piezas comunes de los trabajos de fondo que corren en una VM de pruebas
(cargar una rama, arreglar un problema): log que se guarda en la BD mientras
avanza y ejecución de comandos por SSH con la salida en vivo."""

import asyncio
import time

import asyncssh

from src.config.database import AsyncSessionLocal

LOG_LIMIT = 200_000  # caracteres; se conserva el final
FLUSH_SECONDS = 1.0


class JobLog:
    """Acumula el log de una fila (`model` con columna `log`) y lo guarda como
    mucho una vez por segundo, así el frontend lo ve avanzar sin una escritura
    por línea. Enmascara los secretos indicados."""

    def __init__(self, model, row_id: int, secrets: list[str]):
        self.model = model
        self.row_id = row_id
        self.text = ""
        self.secrets = [s for s in secrets if s]
        self.flushed_at = 0.0

    def add_secrets(self, values) -> None:
        """Más valores a enmascarar (los que aparecen a mitad del trabajo)."""
        self.secrets += [str(v) for v in values if v and len(str(v)) >= 4]

    def _redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    async def write(self, text: str, force: bool = False, **fields) -> None:
        self.text = (self.text + self._redact(text))[-LOG_LIMIT:]
        if force or fields or time.monotonic() - self.flushed_at >= FLUSH_SECONDS:
            async with AsyncSessionLocal() as db:
                row = await db.get(self.model, self.row_id)
                row.log = self.text
                for key, value in fields.items():
                    setattr(row, key, value)
                await db.commit()
            self.flushed_at = time.monotonic()

    async def step(self, step: str, title: str) -> None:
        await self.write(f"\n▶ {title}\n", step=step)


async def run_remote(log: JobLog, conn: asyncssh.SSHClientConnection, command: str, timeout: float) -> int:
    """Corre un comando en la VM y va pasando la salida (stdout+stderr) al log.
    Devuelve el código de salida (124 si se agotó el tiempo)."""
    async with conn.create_process(command, stderr=asyncssh.STDOUT) as process:
        async def pump():
            async for line in process.stdout:
                await log.write(line)
        try:
            await asyncio.wait_for(pump(), timeout)
        except asyncio.TimeoutError:
            process.kill()
            await log.write(f"\n[Tiempo agotado: {int(timeout)} s]\n")
            return 124
        await process.wait()
        return process.exit_status if process.exit_status is not None else 1
