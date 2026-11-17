"""Lógica del tablero, compartida por la API REST y el servidor MCP.

Todas las operaciones que modifican una tarjeta reciben un `Actor` y dejan un
registro en `card_activity`, así se puede ver qué hizo Claude (via=mcp) y qué
hizo una persona desde la app (via=web).

Las posiciones dentro de una lista son enteros consecutivos (0..n-1): al mover
o crear se reindexa la lista completa. Las listas de un tablero de tareas son
chicas, así que es más simple y robusto que posiciones fraccionarias.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.board_list import BoardList
from src.models.card import Card
from src.models.card_activity import CardActivity
from src.models.card_comment import CardComment
from src.models.repository import Repository
from src.models.user import User
from src.services.auth_service import Actor
from src.services import approval_service, github_service, upload_service
from src.services.errors import NotFoundError, ServiceError
from src.services.serializers import (
    activity_dict, card_dict, comment_dict, list_dict, repository_dict, user_brief,
)

_CARD_LOAD = (
    selectinload(Card.board_list),
    selectinload(Card.assignee),
    selectinload(Card.created_by),
    selectinload(Card.repositories),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Lookups ───────────────────────────────────────────────────────────────────

async def get_lists(db: AsyncSession) -> list[BoardList]:
    result = await db.execute(
        select(BoardList).where(BoardList.deleted_at.is_(None)).order_by(BoardList.position, BoardList.id)
    )
    return list(result.scalars())


async def resolve_list(db: AsyncSession, ref: int | str) -> BoardList:
    """Busca una lista por id o por nombre (sin distinguir mayúsculas)."""
    query = select(BoardList).where(BoardList.deleted_at.is_(None))
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        query = query.where(BoardList.id == int(ref))
    else:
        query = query.where(func.lower(BoardList.name) == ref.strip().lower())
    board_list = (await db.execute(query)).scalar_one_or_none()
    if not board_list:
        names = ", ".join(f'"{lst.name}"' for lst in await get_lists(db))
        raise NotFoundError(f'Lista "{ref}" no existe. Listas disponibles: {names}')
    return board_list


async def resolve_repositories(db: AsyncSession, keys: list[str]) -> list[Repository]:
    if not keys:
        return []
    wanted = {k.strip().lower() for k in keys}
    result = await db.execute(
        select(Repository).where(Repository.deleted_at.is_(None), func.lower(Repository.key).in_(wanted))
    )
    repos = list(result.scalars())
    missing = wanted - {r.key.lower() for r in repos}
    if missing:
        all_keys = (await db.execute(select(Repository.key).where(Repository.deleted_at.is_(None)))).scalars()
        raise NotFoundError(
            f"Repositorios inexistentes: {', '.join(sorted(missing))}. Disponibles: {', '.join(sorted(all_keys))}"
        )
    return repos


async def resolve_user(db: AsyncSession, ref: int | str) -> User:
    query = select(User).where(User.deleted_at.is_(None))
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        query = query.where(User.id == int(ref))
    else:
        query = query.where(func.lower(User.username) == ref.strip().lower())
    user = (await db.execute(query)).scalar_one_or_none()
    if not user:
        raise NotFoundError(f'Usuario "{ref}" no existe')
    return user


async def get_card(db: AsyncSession, card_id: int) -> Card:
    result = await db.execute(select(Card).where(Card.id == card_id).options(*_CARD_LOAD))
    card = result.scalar_one_or_none()
    if not card:
        raise NotFoundError(f"Tarjeta #{card_id} no existe")
    return card


async def _comment_counts(db: AsyncSession, card_ids: list[int]) -> dict[int, int]:
    if not card_ids:
        return {}
    result = await db.execute(
        select(CardComment.card_id, func.count())
        .where(CardComment.card_id.in_(card_ids), CardComment.deleted_at.is_(None))
        .group_by(CardComment.card_id)
    )
    return dict(result.all())


# ── Lectura ───────────────────────────────────────────────────────────────────

async def get_board(db: AsyncSession) -> dict:
    lists = await get_lists(db)
    result = await db.execute(
        select(Card).where(Card.archived_at.is_(None)).options(*_CARD_LOAD).order_by(Card.position, Card.id)
    )
    cards = list(result.scalars())
    counts = await _comment_counts(db, [c.id for c in cards])
    by_list: dict[int, list[dict]] = {lst.id: [] for lst in lists}
    for card in cards:
        if card.list_id in by_list:
            by_list[card.list_id].append(card_dict(card, counts.get(card.id, 0)))

    repos = (await db.execute(
        select(Repository).where(Repository.deleted_at.is_(None)).order_by(Repository.key)
    )).scalars()
    users = (await db.execute(
        select(User).where(User.deleted_at.is_(None), User.is_active.is_(True)).order_by(User.name)
    )).scalars()

    return {
        "lists": [{**list_dict(lst), "cards": by_list[lst.id]} for lst in lists],
        "repositories": [repository_dict(r) for r in repos],
        "users": [user_brief(u) for u in users],
    }


async def search_cards(
    db: AsyncSession,
    *,
    list_ref: int | str | None = None,
    repository: str | None = None,
    assignee: str | None = None,
    text: str | None = None,
    archived: bool = False,
    limit: int = 100,
) -> list[dict]:
    query = select(Card).options(*_CARD_LOAD)
    query = query.where(Card.archived_at.is_not(None) if archived else Card.archived_at.is_(None))
    if list_ref is not None:
        query = query.where(Card.list_id == (await resolve_list(db, list_ref)).id)
    if repository:
        query = query.where(Card.repositories.any(func.lower(Repository.key) == repository.strip().lower()))
    if assignee:
        query = query.where(Card.assignee_id == (await resolve_user(db, assignee)).id)
    if text:
        pattern = f"%{text.strip().lower()}%"
        query = query.where(func.lower(Card.title).like(pattern) | func.lower(Card.description).like(pattern))
    query = query.order_by(Card.list_id, Card.position, Card.id).limit(limit)
    cards = list((await db.execute(query)).scalars())
    counts = await _comment_counts(db, [c.id for c in cards])
    return [card_dict(c, counts.get(c.id, 0)) for c in cards]


async def get_card_detail(db: AsyncSession, card_id: int) -> dict:
    card = await get_card(db, card_id)
    comments = (await db.execute(
        select(CardComment)
        .where(CardComment.card_id == card_id, CardComment.deleted_at.is_(None))
        .options(selectinload(CardComment.author))
        .order_by(CardComment.created_at)
    )).scalars()
    activity = (await db.execute(
        select(CardActivity)
        .where(CardActivity.card_id == card_id)
        .options(selectinload(CardActivity.actor))
        .order_by(CardActivity.created_at.desc(), CardActivity.id.desc())
    )).scalars()
    comments = [comment_dict(c) for c in comments]
    return {
        **card_dict(card, len(comments)),
        "comments": comments,
        "activity": [activity_dict(a) for a in activity],
        # Imágenes pegadas en la descripción o en comentarios (el MCP las entrega con get_card_images).
        "images": [
            upload_service.URL_PREFIX + name
            for name in upload_service.referenced_images(card.description, *(c["body"] for c in comments))
        ],
    }


async def user_activity(db: AsyncSession, user_id: int, limit: int = 50, before_id: int | None = None) -> list[dict]:
    """Lo que hizo un usuario en el tablero (en la web, por API o con Claude),
    de lo más nuevo a lo más viejo. Paginado por id: `before_id` es el id de la
    última entrada ya mostrada. Incluye tarjetas archivadas."""
    query = (
        select(CardActivity, Card.title)
        .join(Card, Card.id == CardActivity.card_id)
        .where(CardActivity.actor_id == user_id)
        .order_by(CardActivity.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(CardActivity.id < before_id)
    rows = (await db.execute(query.options(selectinload(CardActivity.actor)))).all()
    return [{**activity_dict(a), "card": {"id": a.card_id, "title": title}} for a, title in rows]


# ── Escritura ─────────────────────────────────────────────────────────────────

def _log(db: AsyncSession, card: Card, actor: Actor, action: str, data: dict | None = None) -> None:
    db.add(CardActivity(card_id=card.id, actor_id=actor.user.id, via=actor.via, action=action, data=data or {}))


async def _reindex(db: AsyncSession, list_id: int, card: Card | None = None, index: int | None = None) -> None:
    """Reescribe las posiciones de una lista. Si se pasa `card`, la inserta en
    `index` (None o fuera de rango = al final)."""
    result = await db.execute(
        select(Card)
        .where(Card.list_id == list_id, Card.archived_at.is_(None))
        .order_by(Card.position, Card.id)
    )
    cards = [c for c in result.scalars() if card is None or c.id != card.id]
    if card is not None:
        index = len(cards) if index is None else max(0, min(index, len(cards)))
        cards.insert(index, card)
    for pos, c in enumerate(cards):
        c.position = pos


async def create_card(
    db: AsyncSession,
    actor: Actor,
    *,
    title: str,
    list_ref: int | str,
    description: str = "",
    repositories: list[str] | None = None,
    assignee: int | str | None = None,
    position: int | None = None,
) -> dict:
    title = (title or "").strip()
    if not title:
        raise ServiceError("El título es obligatorio")
    if not (description or "").strip():
        raise ServiceError("La descripción es obligatoria")
    if not repositories:
        raise ServiceError("Indicá al menos un repositorio")
    board_list = await resolve_list(db, list_ref)
    card = Card(
        title=title,
        description=description,
        list_id=board_list.id,
        created_by_id=actor.user.id,
        # Sin responsable explícito queda asignada a quien la crea.
        assignee_id=(await resolve_user(db, assignee)).id if assignee not in (None, "") else actor.user.id,
        pr_urls=[],
    )
    card.repositories = await resolve_repositories(db, repositories)
    db.add(card)
    await db.flush()
    await _reindex(db, board_list.id, card, position)
    _log(db, card, actor, "created", {"list": board_list.name})
    await db.commit()
    return card_dict(await get_card(db, card.id), 0)


PR_LIST = "PR"


def _pull_comment_body(comment: dict) -> str:
    where = f" en `{comment['path']}`" if comment.get("path") else ""
    return f"**{comment['author']}** comentó{where} en GitHub ({comment['createdAt'][:10]}):\n\n{comment['body']}"


async def _card_ids_by_pr_url(db: AsyncSession) -> dict[str, int]:
    """`{url de PR: id de tarjeta}` de todas las tarjetas, incluso archivadas. pr_urls es JSON: se arma en Python."""
    result = await db.execute(select(Card).where(Card.pr_urls != []).order_by(Card.id.desc()))
    return {url: c.id for c in result.scalars() for url in c.pr_urls or []}


async def open_pulls_with_cards(db: AsyncSession, force: bool = False) -> dict:
    """PRs abiertos (caché de GitHub) con `cardId` = tarjeta que ya los tiene, o None.

    Se calcula en cada lectura y sobre una copia: el caché es compartido y no
    debe guardar datos del tablero.
    """
    data = await github_service.open_pull_requests(db, force=force)
    card_ids = await _card_ids_by_pr_url(db)
    return {**data, "repositories": [
        {**repo, "pulls": [{**pull, "cardId": card_ids.get(pull["url"])} for pull in repo["pulls"]]}
        for repo in data["repositories"]
    ]}


async def create_card_from_pull_request(db: AsyncSession, actor: Actor, repo_key: str, number: int) -> dict:
    """Crea en la lista "PR" la tarjeta de un PR abierto que no tenía una.

    Título y descripción = título del PR; responsable = el usuario cuyo
    `github_login` es el autor del PR (sin coincidencia queda sin responsable);
    el link del PR queda en `pr_urls` y los comentarios del PR pasan a comentarios
    de la tarjeta. Todo en una sola transacción.
    """
    repo = (await resolve_repositories(db, [repo_key]))[0]
    pulls = next(r["pulls"] for r in (await github_service.open_pull_requests(db))["repositories"]
                 if r["key"] == repo.key)
    pull = next((p for p in pulls if p["number"] == number), None)
    if not pull:
        raise NotFoundError(f"El PR #{number} no está abierto en {repo.key}")
    if existing_id := (await _card_ids_by_pr_url(db)).get(pull["url"]):
        raise ServiceError(f"El PR ya tiene la tarjeta #{existing_id}", 409)

    owner, name = github_service.parse_github_url(repo.github_url)
    comments = await github_service.pull_request_comments(owner, name, number)
    board_list = await resolve_list(db, PR_LIST)
    login = pull["author"]["login"].lower()
    author = (await db.execute(
        select(User).where(User.deleted_at.is_(None), func.lower(User.github_login) == login)
    )).scalars().first()

    card = Card(
        title=pull["title"][:255],
        description=pull["title"],
        list_id=board_list.id,
        created_by_id=actor.user.id,
        assignee_id=author.id if author else None,
        pr_urls=[pull["url"]],
    )
    card.repositories = [repo]
    db.add(card)
    await db.flush()
    await _reindex(db, board_list.id, card, None)
    _log(db, card, actor, "created", {"list": board_list.name, "fromPr": pull["url"]})
    _log(db, card, actor, "pr_added", {"url": pull["url"]})
    for comment in comments:
        db.add(CardComment(card_id=card.id, author_id=actor.user.id, body=_pull_comment_body(comment), via=actor.via))
    await db.commit()
    return card_dict(await get_card(db, card.id), len(comments))


async def update_card(db: AsyncSession, actor: Actor, card_id: int, changes: dict) -> dict:
    """`changes` acepta: title, description, repositories (keys),
    assignee (id/username o None para quitarlo), prUrls (lista).
    Solo se tocan las claves presentes."""
    card = await get_card(db, card_id)
    touched: list[str] = []

    if "title" in changes:
        title = (changes["title"] or "").strip()
        if not title:
            raise ServiceError("El título es obligatorio")
        if title != card.title:
            card.title = title
            touched.append("title")
    if "description" in changes and (changes["description"] or "") != card.description:
        card.description = changes["description"] or ""
        touched.append("description")
    if "repositories" in changes:
        repos = await resolve_repositories(db, changes["repositories"] or [])
        if {r.id for r in repos} != {r.id for r in card.repositories}:
            card.repositories = repos
            touched.append("repositories")
    if "assignee" in changes:
        ref = changes["assignee"]
        assignee_id = (await resolve_user(db, ref)).id if ref not in (None, "") else None
        if assignee_id != card.assignee_id:
            card.assignee_id = assignee_id
            touched.append("assignee")
    if "prUrls" in changes:
        urls = [u.strip() for u in (changes["prUrls"] or []) if u and u.strip()]
        if urls != list(card.pr_urls or []):
            card.pr_urls = urls
            touched.append("prUrls")

    if touched:
        _log(db, card, actor, "updated", {"fields": touched})
        await db.commit()
        db.expire(card)
    return card_dict(await get_card(db, card_id))


async def move_card(
    db: AsyncSession, actor: Actor, card_id: int, list_ref: int | str, position: int | None = None
) -> dict:
    card = await get_card(db, card_id)
    if card.archived_at:
        raise ServiceError(f"La tarjeta #{card_id} está archivada; desarchivala antes de moverla")
    target = await resolve_list(db, list_ref)
    source_id = card.list_id
    if source_id != target.id:
        source = await resolve_list(db, source_id)
        card.list_id = target.id
        await _reindex(db, source_id, None)
        _log(db, card, actor, "moved", {"from": source.name, "to": target.name})
    await _reindex(db, target.id, card, position)
    await db.commit()
    db.expire(card)
    return card_dict(await get_card(db, card_id))


async def set_archived(db: AsyncSession, actor: Actor, card_id: int, archived: bool) -> dict:
    card = await get_card(db, card_id)
    if bool(card.archived_at) == archived:
        return card_dict(card)
    card.archived_at = _now() if archived else None
    await db.flush()
    await _reindex(db, card.list_id, card if not archived else None)
    _log(db, card, actor, "archived" if archived else "unarchived")
    await db.commit()
    db.expire(card)
    return card_dict(await get_card(db, card_id))


async def add_comment(db: AsyncSession, actor: Actor, card_id: int, body: str) -> dict:
    card = await get_card(db, card_id)
    body = (body or "").strip()
    if not body:
        raise ServiceError("El comentario no puede estar vacío")
    comment = CardComment(card_id=card.id, author_id=actor.user.id, body=body, via=actor.via)
    db.add(comment)
    await db.flush()
    _log(db, card, actor, "commented", {"commentId": comment.id})
    await db.commit()
    result = await db.execute(
        select(CardComment).where(CardComment.id == comment.id).options(selectinload(CardComment.author))
    )
    return comment_dict(result.scalar_one())


async def delete_comment(db: AsyncSession, actor: Actor, card_id: int, comment_id: int) -> None:
    result = await db.execute(
        select(CardComment).where(
            CardComment.id == comment_id, CardComment.card_id == card_id, CardComment.deleted_at.is_(None)
        )
    )
    comment = result.scalar_one_or_none()
    if not comment:
        raise NotFoundError("Comentario no encontrado")
    if comment.author_id != actor.user.id and actor.user.role != "admin":
        raise ServiceError("Solo el autor o un admin puede borrar el comentario", 403)
    comment.deleted_at = _now()
    await db.commit()


async def qa_review(
    db: AsyncSession, actor: Actor, card_id: int, approved: bool, notes: str | None, list_ref: int | str | None = None
) -> dict:
    """Veredicto de QA sobre los PRs de la tarjeta.

    Aprobar pone el label "Aprobado" en sus PRs abiertos de GitHub (quedan
    listos para "Mergear PR") y deja un comentario; **no mueve la tarjeta**: sigue
    siendo un PR hasta que se mergea. Rechazar exige notas, devuelve sus PRs
    abiertos a "Revisado" (les saca "Aprobado" y les pone "Revisado"; siguen
    abiertos), deja el comentario y mueve la tarjeta a `list_ref` (quien la
    retome tiene que saber qué falló).

    Se valida y se escribe en GitHub antes de tocar la tarjeta, para no dejar el
    comentario sin el resto. Los dos efectos en GitHub son idempotentes: si algún
    PR falla no se escribe nada en la tarjeta y se puede reintentar.
    """
    card = await get_card(db, card_id)
    if card.archived_at:
        raise ServiceError(f"La tarjeta #{card_id} está archivada")
    notes = (notes or "").strip()
    if not approved:
        if not notes:
            raise ServiceError("Para rechazar, indicá qué falló en la prueba")
        if list_ref is None:
            raise ServiceError("Para rechazar, elegí la lista a la que vuelve la tarjeta")
        await resolve_list(db, list_ref)

    # Sin PRs de GitHub no se escribe nada allá: no hace falta el token.
    token = github_service.user_token(actor.user) if approval_service.github_pull_urls(card.pr_urls or []) else ""
    if approved:
        results = await approval_service.label_pull_requests(card.pr_urls or [], token)
        ok_status, fail_verb = "labeled", f'poner el label "{approval_service.APPROVED_LABEL}"'
    else:
        results = await approval_service.reject_pull_requests(card.pr_urls or [], token)
        ok_status, fail_verb = "rejected", f'devolver el PR a "{approval_service.REVIEWED_LABEL}"'
    failed = [r for r in results if r["status"] == "failed"]
    if failed:
        detail = "; ".join(f"{r['url']}: {r['message']}" for r in failed)
        raise ServiceError(f"No se pudo {fail_verb} (la tarjeta no se tocó): {detail}", 502)
    done = [r["url"] for r in results if r["status"] == ok_status]
    if approved and not done:
        raise ServiceError("No hay PRs abiertos de GitHub para aprobar: registrá el PR en la tarjeta", 400)

    header = "✅ **QA aprobado**" if approved else "❌ **QA rechazado**"
    body = f"{header}\n\n{notes}" if notes else header
    if approved:
        body += f'\n\nLabel **{approval_service.APPROVED_LABEL}** en: ' + ", ".join(done)
        _log(db, card, actor, "approved", {"label": approval_service.APPROVED_LABEL, "pulls": done})
    elif done:
        body += f'\n\nPRs devueltos a **{approval_service.REVIEWED_LABEL}**: ' + ", ".join(done)
        _log(db, card, actor, "rejected", {"label": approval_service.REVIEWED_LABEL, "pulls": done})
    await add_comment(db, actor, card_id, body)
    if approved:
        return card_dict(await get_card(db, card_id))
    return await move_card(db, actor, card_id, list_ref)


DONE_LIST = "Hecho"
MERGE_ICONS = {"merged": "✅", "rebased": "🔄", "conflict": "⚠️", "skipped": "⏭️", "failed": "❌"}


async def record_merge(db: AsyncSession, actor: Actor, card_id: int, results: list[dict]) -> tuple[dict, bool]:
    """Deja en la tarjeta (comentario + historial) el resultado de "Mergear PR"
    por PR. Si todos los PRs quedaron mergeados la pasa a Hecho; con uno solo
    salteado o fallido se queda donde está. Devuelve (tarjeta, movida_a_hecho)."""
    card = await get_card(db, card_id)
    merged = sum(1 for r in results if r["status"] == "merged")
    lines = [f"**Mergear PR**: {merged} de {len(results)} mergeado(s)", ""]
    for r in results:
        branch = f" (`{r['branch']}` → `{r['base']}`)" if r.get("branch") else ""
        lines.append(f"- {MERGE_ICONS.get(r['status'], '•')} {r['url']}{branch}: {r['message']}")
    _log(db, card, actor, "merge_attempted",
         {"results": [{"url": r["url"], "status": r["status"]} for r in results]})
    await add_comment(db, actor, card_id, "\n".join(lines))
    moved = False
    if results and merged == len(results):
        try:
            done = await resolve_list(db, DONE_LIST)
        except NotFoundError:
            done = None  # el tablero no tiene lista "Hecho": se queda donde está
        if done and card.list_id != done.id:
            await move_card(db, actor, card_id, done.id)
            moved = True
    db.expire(card)
    return card_dict(await get_card(db, card_id)), moved


async def record_rebase(db: AsyncSession, actor: Actor, card_id: int, results: list[dict]) -> dict:
    """Deja en la tarjeta (comentario + historial) el resultado de "Rebase" por PR.
    No mueve la tarjeta."""
    card = await get_card(db, card_id)
    rebased = sum(1 for r in results if r["status"] == "rebased")
    lines = [f"**Rebase**: {rebased} de {len(results)} PR(s) al día con su rama base", ""]
    for r in results:
        branch = f" (`{r['branch']}` → `{r['base']}`)" if r.get("branch") else ""
        lines.append(f"- {MERGE_ICONS.get(r['status'], '•')} {r['url']}{branch}: {r['message']}")
    _log(db, card, actor, "rebase_attempted",
         {"results": [{"url": r["url"], "status": r["status"]} for r in results]})
    await add_comment(db, actor, card_id, "\n".join(lines))
    db.expire(card)
    return card_dict(await get_card(db, card_id))


async def add_pr_url(db: AsyncSession, actor: Actor, card_id: int, url: str) -> dict:
    card = await get_card(db, card_id)
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ServiceError("El link del PR debe ser una URL http(s)")
    if url not in (card.pr_urls or []):
        # Reasignar la lista (no mutarla) para que SQLAlchemy detecte el cambio del JSON.
        card.pr_urls = [*(card.pr_urls or []), url]
        _log(db, card, actor, "pr_added", {"url": url})
        await db.commit()
        db.expire(card)
    return card_dict(await get_card(db, card_id))
