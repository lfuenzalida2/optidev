"""La documentación de cada repo para quien va a trabajar en él: `repo-docs/<repo>/` del repo de agentes
(el del tablero con key WORKFLOW_REPO_KEY). Se lee en vivo de GitHub con `workflow_service`, que ya resuelve el
token, la rama base, el caché y el tope de tamaño; no se guarda nada acá."""

from sqlalchemy.ext.asyncio import AsyncSession

from src.services import board_service, workflow_service
from src.services.errors import NotFoundError, ServiceError
from src.services.github_service import parse_github_url

DOCS_DIR = "repo-docs"
# Los archivos de cada carpeta además del README (ver repo-docs/README.md del repo de agentes).
SECTIONS = ("architecture", "data", "integrations", "development", "conventions")


async def get_docs(db: AsyncSession, repo_key: str, section: str | None = None) -> dict:
    """README del repo (con su «Antes de modificar») o una de sus secciones. La carpeta se llama como el repo en
    GitHub, no como su key del tablero."""
    if section is not None and section not in SECTIONS:
        raise ServiceError(f"Sección desconocida '{section}'. Sin `section` se devuelve el README; "
                           f"opciones: {', '.join(SECTIONS)}")
    repo = (await board_service.resolve_repositories(db, [repo_key]))[0]
    gh = parse_github_url(repo.github_url)
    if not gh:
        raise ServiceError(f"El repositorio '{repo.key}' no tiene una URL de GitHub válida")
    path = f"{DOCS_DIR}/{gh[1]}/{section or 'README'}.md"
    try:
        doc = await workflow_service.read_file(db, path)
    except NotFoundError:
        raise NotFoundError(f"El repositorio '{repo.key}' no tiene documentación en {path} del repo de agentes") from None
    return {"repo": repo.key, "section": section or "README", "path": path, "githubUrl": doc["githubUrl"],
            "content": doc["content"], "tooLarge": doc["tooLarge"]}
