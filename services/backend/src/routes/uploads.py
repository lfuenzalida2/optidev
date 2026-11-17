from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse

from src.config.settings import settings
from src.middleware.auth import CurrentActor
from src.services import upload_service

router = APIRouter()


@router.post("", status_code=201)
async def upload_image(file: UploadFile, _: CurrentActor):
    # Se lee un byte de más para detectar archivos sobre el límite sin cargarlos enteros.
    data = await file.read(settings.UPLOAD_MAX_MB * 1024 * 1024 + 1)
    return {"success": True, "data": upload_service.save_image(data)}


@router.get("/{name}")
async def get_image(name: str):
    """Público a propósito: ver el docstring de upload_service."""
    path = upload_service.path_for(name)
    return FileResponse(
        path,
        media_type=upload_service.CONTENT_TYPES[path.suffix[1:]],
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
