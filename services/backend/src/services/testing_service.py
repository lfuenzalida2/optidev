"""Cola de testing/QA: tarjetas de las listas de revisión con el estado en vivo
de sus PRs en GitHub.

Si GitHub no responde (o falta GITHUB_TOKEN) la cola se devuelve igual, sin el
detalle de los PRs y con `githubError`: se puede seguir haciendo QA aunque no
se vea el estado de los checks.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from src.models.card import Card
from src.services import board_service, github_service
from src.services.errors import ServiceError
from src.services.serializers import card_dict, list_dict

# Listas que se muestran si no se elige ninguna (por nombre, sin mayúsculas).
DEFAULT_LISTS = ("pr", "testing")


async def testing_queue(db, list_ids: list[int] | None = None, force: bool = False) -> dict:
    lists = await board_service.get_lists(db)
    if list_ids:
        selected = [lst for lst in lists if lst.id in set(list_ids)]
    else:
        selected = [lst for lst in lists if lst.name.strip().lower() in DEFAULT_LISTS]
    order = {lst.id: i for i, lst in enumerate(selected)}

    cards = []
    if selected:
        result = await db.execute(
            select(Card).where(Card.archived_at.is_(None), Card.list_id.in_(order))
            .options(*board_service._CARD_LOAD).order_by(Card.position, Card.id)
        )
        cards = sorted(result.scalars(), key=lambda c: order[c.list_id])
    counts = await board_service._comment_counts(db, [c.id for c in cards])

    urls = [url for card in cards for url in card.pr_urls or []]
    details, github_error = {}, None
    if urls:
        try:
            details = await github_service.pull_request_details(urls, force=force)
        except ServiceError as exc:
            github_error = exc.message

    return {
        "lists": [list_dict(lst) for lst in lists],
        "listIds": [lst.id for lst in selected],
        "cards": [
            {**card_dict(card, counts.get(card.id, 0)),
             "pulls": [details.get(url) or {"url": url, "error": None} for url in card.pr_urls or []]}
            for card in cards
        ],
        "githubError": github_error,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
