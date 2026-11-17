# Optidev — notas para Claude

Tablero kanban interno (FastAPI + React) con servidor MCP. Ver README.md.

## Estructura

- `services/backend/src/services/board_service.py`: **toda** la lógica de
  tarjetas. La usan tanto las rutas REST (`src/routes/cards.py`) como el MCP
  (`src/mcp_server/server.py`). Un cambio de comportamiento va acá, no en las
  rutas ni en las tools.
- Toda escritura sobre una tarjeta recibe un `Actor` y registra una entrada en
  `card_activity` (`via` = web | api | mcp).
- Errores de negocio: lanzar `ServiceError`/`NotFoundError` (src/services/errors.py).
  REST los traduce a HTTP; el MCP a `ToolError`.
- `src/app.py` despacha `/mcp` al servidor MCP y el resto a FastAPI (no usar
  `api.mount`, genera un redirect `/mcp` → `/mcp/`).

## Convenciones

- Python 3.11, imports absolutos desde `src.`, async en todo el acceso a BD.
- Respuestas REST: `{"success": True, "data": ..., "message": ...}`; JSON en camelCase.
- Soft delete (`deleted_at`) en usuarios, repos, listas y
  comentarios; las tarjetas se archivan (`archived_at`).
- Unicidad con soft delete = índices únicos parciales (`sqlite_where=text(...)`).
- Fechas: SQLite las devuelve naive; siempre son UTC (ver `_iso` en serializers).
- Cambios de modelo → `alembic revision --autogenerate` y revisar el archivo
  generado. CI corre `alembic check`.
- Frontend: JSX, antd, llamadas a la API solo vía `src/api/index.js`.

## Verificar

```bash
cd services/backend && .venv/bin/ruff check . && .venv/bin/pytest -q
cd services/frontend && yarn lint && yarn build
```
