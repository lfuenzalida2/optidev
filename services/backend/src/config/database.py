from sqlalchemy import Index, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config.settings import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.DATABASE_URL, echo=False)

if engine.dialect.name == "sqlite":
    # WAL deja leer mientras otro escribe (UI + MCP en paralelo) y SQLite no
    # aplica las FK si no se le pide explícitamente en cada conexión.
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()



def active_unique_index(name: str, column: str) -> Index:
    """Único solo entre filas no borradas (soft delete: `deleted_at IS NULL`).

    SQLite usa un índice parcial; MySQL no los tiene, así que se indexa una
    expresión que vale NULL en las borradas (y NULL no choca en un único).
    Las migraciones replican esto en `0001` y deben mantenerse iguales.
    """
    if engine.dialect.name == "mysql":
        return Index(name, text(f"(CASE WHEN deleted_at IS NULL THEN `{column}` END)"), unique=True)
    return Index(name, column, unique=True, sqlite_where=text("deleted_at IS NULL"))


AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
