import pytest

from src.services import workflow_service
from src.services.errors import NotFoundError
from tests.test_mcp import _call, _payload, _token

HELIOS = "repo-docs/optidev-helios"
# None = el archivo existe pero supera el tope de tamaño.
DOCS = {
    f"{HELIOS}/README.md": "# helios\n\n## Antes de modificar\n\n- una trampa\n",
    f"{HELIOS}/data.md": "# datos\n",
    f"{HELIOS}/architecture.md": None,
}


@pytest.fixture
def docs(monkeypatch):
    """Lecturas del repo de agentes simuladas; devuelve las rutas que se pidieron."""
    reads = []

    async def read_file(db, path, ref=None):
        reads.append(path)
        if path not in DOCS:
            raise NotFoundError(f"No existe el archivo '{path}'")
        text = DOCS[path]
        return {"path": path, "content": text, "tooLarge": text is None, "binary": False, "size": len(text or ""),
                "githubUrl": f"https://github.com/org/optidev-agents/blob/abc/{path}"}

    monkeypatch.setattr(workflow_service, "read_file", read_file)
    return reads


async def _repo(admin, key, url=None):
    resp = await admin.post("/api/repositories", json={"key": key, "name": key, "githubUrl": url})
    assert resp.status_code == 201, resp.text


async def test_get_repo_docs_returns_the_readme_and_each_section(admin, docs):
    await _repo(admin, "helios", "https://github.com/org/optidev-helios")
    token = await _token(admin)

    readme = _payload(await _call(admin, token, "get_repo_docs", {"repo": "helios"}))
    # La carpeta se llama como el repo en GitHub (optidev-helios), no como su key del tablero (helios).
    assert readme["repo"] == "helios" and readme["section"] == "README" and readme["path"] == f"{HELIOS}/README.md"
    assert "Antes de modificar" in readme["content"] and readme["tooLarge"] is False
    assert readme["githubUrl"].endswith(f"{HELIOS}/README.md")

    section = _payload(await _call(admin, token, "get_repo_docs", {"repo": "Helios", "section": "data"}))
    assert section["section"] == "data" and section["content"] == "# datos\n"
    assert docs == [f"{HELIOS}/README.md", f"{HELIOS}/data.md"]

    # Un archivo que supera el tope se informa sin contenido, con el enlace para verlo en GitHub.
    big = _payload(await _call(admin, token, "get_repo_docs", {"repo": "helios", "section": "architecture"}))
    assert big["content"] is None and big["tooLarge"] is True and big["githubUrl"]


async def test_get_repo_docs_errors(admin, docs):
    await _repo(admin, "helios", "https://github.com/org/optidev-helios")
    await _repo(admin, "sin-url")
    await _repo(admin, "sin-docs", "https://github.com/org/nuevo")
    token = await _token(admin)

    async def error(args):
        result = await _call(admin, token, "get_repo_docs", args)
        assert result["isError"] is True
        return result["content"][0]["text"]

    assert "inexistentes: no-existe" in await error({"repo": "no-existe"})
    assert "URL de GitHub" in await error({"repo": "sin-url"})
    assert "repo-docs/nuevo/README.md" in await error({"repo": "sin-docs"})
    # Solo las secciones conocidas: no se puede pedir cualquier ruta del repo de agentes.
    message = await error({"repo": "helios", "section": "../../README"})
    assert "Sección desconocida" in message and "architecture" in message
    assert docs == ["repo-docs/nuevo/README.md"]  # lo único que llegó a pedirse a GitHub
