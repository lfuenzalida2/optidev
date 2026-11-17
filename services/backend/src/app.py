import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.mcp_server.server import mcp, mcp_asgi
from src.routes import auth, cards, catalog, github, notifications, preview_dbs, preview_env, previews, repo_health, system, tokens, tools, uploads, users, vms, workflow
from src.services.bootstrap import bootstrap
from src.services import github_service, preview_service, repo_health_service, vm_doctor_service
from src.services.errors import ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await bootstrap()
    await preview_service.fail_interrupted()
    await vm_doctor_service.fail_interrupted()
    pulls_refresher = asyncio.create_task(github_service.refresh_pulls_loop())
    health_monitor = asyncio.create_task(repo_health_service.monitor_loop())
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        pulls_refresher.cancel()
        health_monitor.cancel()


api = FastAPI(title="Optidev", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


@api.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status_code, content={"success": False, "detail": exc.message})


@api.get("/api/health")
async def health():
    return {"status": "ok"}


api.include_router(auth.router, prefix="/api/auth", tags=["auth"])
api.include_router(users.router, prefix="/api/users", tags=["users"])
api.include_router(tokens.router, prefix="/api/tokens", tags=["tokens"])
api.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
api.include_router(vms.router, prefix="/api/vms", tags=["vms"])
api.include_router(previews.router, prefix="/api/previews", tags=["previews"])
api.include_router(preview_env.router, prefix="/api/preview-env", tags=["preview-env"])
api.include_router(preview_dbs.router, prefix="/api/preview-dbs", tags=["preview-dbs"])
api.include_router(catalog.router, prefix="/api", tags=["catalog"])
api.include_router(cards.router, prefix="/api", tags=["cards"])
api.include_router(uploads.router, prefix="/api/uploads", tags=["uploads"])
api.include_router(github.router, prefix="/api/github", tags=["github"])
api.include_router(repo_health.router, prefix="/api/repo-health", tags=["repo-health"])
api.include_router(workflow.router, prefix="/api/workflow", tags=["workflow"])
api.include_router(tools.router, prefix="/api/tools", tags=["tools"])
api.include_router(system.router, prefix="/api/system", tags=["system"])


async def app(scope, receive, send):
    """Punto de entrada ASGI: /mcp va al servidor MCP y el resto a FastAPI.
    Se despacha acá (y no con `api.mount`) para servir exactamente `/mcp` sin
    el redirect a `/mcp/` que genera un Mount de Starlette."""
    if scope["type"] in ("http", "websocket") and (scope["path"] == "/mcp" or scope["path"].startswith("/mcp/")):
        await mcp_asgi(scope, receive, send)
    else:
        await api(scope, receive, send)
