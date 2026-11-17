"""Servidor MCP del tablero, para que Claude Code lea y opere las tarjetas.

Se sirve en `/mcp` (streamable HTTP, stateless) y exige un token personal
`odv_...` en `Authorization: Bearer`. El token se valida dos veces: en
`McpAuthGate` (rechaza con 401 antes de llegar al protocolo) y en cada tool
(para saber qué usuario es el actor). Todo lo que hace Claude queda en el
historial de la tarjeta con via="mcp".

Registro en Claude Code:
    claude mcp add --transport http optidev https://<host>/mcp \
        --header "Authorization: Bearer odv_..."
"""

import json
from datetime import datetime, timezone

from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from src.config.database import AsyncSessionLocal
from src.services import board_service, repo_docs_service, upload_service
from src.services.auth_service import API_TOKEN_PREFIX, Actor, resolve_api_token
from src.services.errors import ServiceError
from src.services.serializers import user_dict

INSTRUCTIONS = """\
Tablero kanban de tareas de desarrollo de Optidev (optidev).

- Cada tarjeta tiene `repositories`: las keys de los repos donde hay que
  trabajar. Usá `get_board_info` para ver, por repo, la URL de GitHub, la rama
  base (`defaultBranch`) y la ruta local (`localPath`; vacía = no hay clon
  accesible).
- Las listas reflejan el estado de la tarea (por defecto: Pendiente, En
  proceso, PR, Testing, Hecho). Se referencian por nombre o id.
- Al empezar una tarea movela a "En proceso"; al abrir el PR registrá el link
  con `add_pr_url` y movela a "PR". No la muevas a "Hecho": eso lo hace una
  persona tras revisar.
- Las dudas o el resumen de lo hecho van como comentario (`add_comment`).
- Si `get_card` trae `images` (capturas pegadas en la descripción o en
  comentarios), miralas con `get_card_images` antes de empezar: suelen
  mostrar el bug o el diseño esperado.
- Antes de tocar un repo, leé su documentación con `get_repo_docs`: el README
  trae «Antes de modificar» (trampas ya verificadas en el código).
- Todo cambio queda registrado a nombre del dueño del token, con via=mcp.
"""

mcp = MCPServer(name="optidev", title="Optidev board", instructions=INSTRUCTIONS)


async def _actor(ctx: Context, db) -> Actor:
    auth = (ctx.headers or {}).get("authorization", "")
    raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    user = await resolve_api_token(db, raw) if raw.startswith(API_TOKEN_PREFIX) else None
    if not user:
        raise ToolError("Token inválido o revocado")
    return Actor(user=user, via="mcp")


def _err(exc: ServiceError) -> ToolError:
    return ToolError(exc.message)


def _brief(card: dict) -> dict:
    """Versión compacta de una tarjeta para listados (sin descripción)."""
    return {
        "id": card["id"],
        "title": card["title"],
        "list": card["listName"],
        "repositories": [r["key"] for r in card["repositories"]],
        "assignee": card["assignee"]["username"] if card["assignee"] else None,
        "prUrls": card["prUrls"],
        "commentCount": card.get("commentCount", 0),
        "updatedAt": card["updatedAt"],
    }


@mcp.tool()
async def whoami(ctx: Context) -> dict:
    """Usuario dueño del token con el que se está operando."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        return user_dict(actor.user)


@mcp.tool()
async def get_board_info(ctx: Context) -> dict:
    """Listas (con cantidad de tarjetas), repositorios (key, GitHub, rama base,
    ruta local) y usuarios del tablero."""
    async with AsyncSessionLocal() as db:
        await _actor(ctx, db)
        board = await board_service.get_board(db)
        return {
            "lists": [{"id": lst["id"], "name": lst["name"], "cardCount": len(lst["cards"])} for lst in board["lists"]],
            "repositories": board["repositories"],
            "users": board["users"],
        }


@mcp.tool()
async def list_cards(
    ctx: Context,
    list: str | None = None,
    repository: str | None = None,
    assignee: str | None = None,
    text: str | None = None,
    archived: bool = False,
) -> list[dict]:
    """Lista tarjetas (sin descripción) filtrando opcionalmente por lista
    (nombre o id), repositorio (key), responsable (username), texto
    en título/descripción, o archivadas. Ordenadas por lista y posición."""
    async with AsyncSessionLocal() as db:
        await _actor(ctx, db)
        try:
            cards = await board_service.search_cards(
                db, list_ref=list, repository=repository, assignee=assignee, text=text, archived=archived,
            )
        except ServiceError as exc:
            raise _err(exc)
        return [_brief(c) for c in cards]


@mcp.tool()
async def get_card(ctx: Context, card_id: int) -> dict:
    """Detalle completo de una tarjeta: descripción, repos, PRs,
    comentarios e historial."""
    async with AsyncSessionLocal() as db:
        await _actor(ctx, db)
        try:
            return await board_service.get_card_detail(db, card_id)
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def get_repo_docs(ctx: Context, repo: str, section: str | None = None) -> dict:
    """Documentación de un repo (carpeta `repo-docs/<repo>/` del repo de agentes). Sin `section` devuelve el
    README, que trae «Antes de modificar»; con `section`: architecture, data, integrations, development o
    conventions. `repo` es la key del tablero (ver `get_board_info`)."""
    async with AsyncSessionLocal() as db:
        await _actor(ctx, db)
        try:
            return await repo_docs_service.get_docs(db, repo, section)
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def get_card_images(ctx: Context, card_id: int) -> list[Image]:
    """Imágenes pegadas en la descripción y los comentarios de la tarjeta (las
    URLs de `images` en `get_card`), en el orden en que aparecen."""
    async with AsyncSessionLocal() as db:
        await _actor(ctx, db)
        try:
            detail = await board_service.get_card_detail(db, card_id)
        except ServiceError as exc:
            raise _err(exc)
    images = []
    for url in detail["images"]:
        try:
            path = upload_service.path_for(url.removeprefix(upload_service.URL_PREFIX))
        except ServiceError:
            continue  # referenciada pero ya no está en disco
        images.append(Image(path=path))
    return images


@mcp.tool()
async def create_card(
    ctx: Context,
    title: str,
    description: str,
    repositories: list[str],
    list: str = "Pendiente",
    assignee: str | None = None,
) -> dict:
    """Crea una tarjeta al final de la lista indicada. `description` y al
    menos un repo en `repositories` (keys) son obligatorios. Sin `assignee`
    queda asignada al dueño del token."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        try:
            card = await board_service.create_card(
                db, actor, title=title, list_ref=list, description=description,
                repositories=repositories, assignee=assignee,
            )
        except ServiceError as exc:
            raise _err(exc)
        return _brief(card)


@mcp.tool()
async def update_card(
    ctx: Context,
    card_id: int,
    title: str | None = None,
    description: str | None = None,
    repositories: list[str] | None = None,
    assignee: str | None = None,
    unassign: bool = False,
) -> dict:
    """Edita una tarjeta. Solo se cambian los campos enviados; `repositories`
    reemplaza la lista completa. `unassign=true` quita el responsable."""
    changes = {k: v for k, v in {
        "title": title, "description": description, "repositories": repositories,
        "assignee": assignee,
    }.items() if v is not None}
    if unassign:
        changes["assignee"] = None
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        try:
            return _brief(await board_service.update_card(db, actor, card_id, changes))
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def move_card(ctx: Context, card_id: int, list: str, position: int | None = None) -> dict:
    """Mueve una tarjeta a otra lista (nombre o id). Sin `position` queda al
    final; `position=0` la deja primera."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        try:
            return _brief(await board_service.move_card(db, actor, card_id, list, position))
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def add_comment(ctx: Context, card_id: int, body: str) -> dict:
    """Agrega un comentario (markdown) a la tarjeta."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        try:
            return await board_service.add_comment(db, actor, card_id, body)
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def add_pr_url(ctx: Context, card_id: int, url: str) -> dict:
    """Registra el link de un PR en la tarjeta (no duplica si ya está)."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        try:
            return _brief(await board_service.add_pr_url(db, actor, card_id, url))
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def archive_card(ctx: Context, card_id: int) -> dict:
    """Archiva una tarjeta (no se borra; se puede restaurar desde la app)."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        try:
            return _brief(await board_service.set_archived(db, actor, card_id, True))
        except ServiceError as exc:
            raise _err(exc)


@mcp.tool()
async def report_routine_run(ctx: Context) -> dict:
    """Registra que corrió la rutina diaria del usuario (la de la 1:00 que
    revisa sus tarjetas). Llamala al inicio de cada corrida, también cuando se
    corre a mano para probarla: así optidev la da por configurada."""
    async with AsyncSessionLocal() as db:
        actor = await _actor(ctx, db)
        actor.user.routine_last_run_at = datetime.now(timezone.utc)
        await db.commit()
        return {"routineLastRunAt": user_dict(actor.user)["routineLastRunAt"]}


# La auth es por token bearer, así que la protección contra DNS rebinding (que
# apunta a servidores locales sin auth abiertos desde un navegador) no aplica
# y bloquearía el acceso por el hostname real detrás de nginx/Traefik.
mcp_http_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


class McpAuthGate:
    """ASGI wrapper: corta con 401 cualquier request a /mcp sin token válido."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            user = None
            if raw.startswith(API_TOKEN_PREFIX):
                async with AsyncSessionLocal() as db:
                    user = await resolve_api_token(db, raw)
            if not user:
                body = json.dumps({"detail": "Token MCP inválido: usá un token personal odv_... "
                                             "(Mi perfil y tokens, en la app)"}).encode()
                # Sin WWW-Authenticate a propósito: con ese header Claude Code
                # intenta descubrir un servidor OAuth que no existe.
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"),
                ]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


mcp_asgi = McpAuthGate(mcp_http_app)
