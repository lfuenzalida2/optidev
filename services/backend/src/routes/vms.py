from fastapi import APIRouter, Depends, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.database import get_db
from src.middleware.auth import AdminActor, CurrentActor
from src.schemas.requests import SkillReplyBody, SkillRunBody, TestVmBody
from src.services import skill_service, terminal_service, vm_doctor_service, vm_request_service, vm_service

router = APIRouter()


@router.get("")
async def list_vms(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await vm_service.list_vms(db, actor)}


@router.get("/all")
async def list_all_vms(_: AdminActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await vm_service.list_all_vms(db)}


@router.get("/requests")
async def list_vm_requests(_: AdminActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await vm_request_service.list_pending(db)}


@router.get("/request")
async def get_vm_request(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """La solicitud de VM abierta del usuario (null si no tiene)."""
    return {"success": True, "data": await vm_request_service.get_pending(db, actor)}


@router.post("/request", status_code=201)
async def request_vm(actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await vm_request_service.request_vm(db, actor)
    return {"success": True, "data": data, "message": "Solicitud enviada a los admins"}


@router.post("", status_code=201)
async def create_vm(body: TestVmBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await vm_service.create_vm(db, actor, body.model_dump(), owner_id=body.userId)
    return {"success": True, "data": data, "message": "VM guardada"}


@router.post("/test")
async def test_draft(body: TestVmBody, actor: CurrentActor, db: AsyncSession = Depends(get_db), vmId: int | None = None):
    """Prueba de conexión con los datos del formulario, sin guardar."""
    return {"success": True, "data": await vm_service.test_draft(db, actor, body.model_dump(), vmId)}


@router.patch("/{vm_id}")
async def update_vm(vm_id: int, body: TestVmBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    data = await vm_service.update_vm(db, actor, vm_id, body.model_dump())
    return {"success": True, "data": data, "message": "VM actualizada"}


@router.delete("/{vm_id}")
async def delete_vm(vm_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    await vm_service.delete_vm(db, actor, vm_id)
    return {"success": True, "message": "VM eliminada"}


@router.post("/{vm_id}/check")
async def check_connection(vm_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await vm_service.check_connection(db, actor, vm_id)}


@router.websocket("/{vm_id}/terminal")
async def terminal(websocket: WebSocket, vm_id: int):
    # La autenticación va en el primer mensaje (ver terminal_service), no en Depends.
    await terminal_service.run_terminal(websocket, vm_id)


@router.post("/{vm_id}/diagnose")
async def diagnose(vm_id: int, request: Request, actor: CurrentActor, db: AsyncSession = Depends(get_db),
                   group: str = "doctor"):
    """Chequea los problemas conocidos de la VM (git, Docker, disco...). `group`=claude_rc / routine: los
    pasos de las herramientas de Claude Remote Control y de la rutina diaria."""
    data = await vm_doctor_service.diagnose(db, actor, vm_id, group, str(request.base_url))
    return {"success": True, "data": data}


@router.post("/{vm_id}/fixes/{issue_id}", status_code=202)
async def start_fix(vm_id: int, issue_id: str, request: Request, actor: CurrentActor,
                    db: AsyncSession = Depends(get_db)):
    """Arregla un problema del catálogo (el cliente solo elige el id, no manda comandos). La URL de la
    petición es la de optidev que se registra (y se chequea) como MCP en la VM (si no hay PUBLIC_URL)."""
    data = await vm_doctor_service.start_fix(db, actor, vm_id, issue_id, str(request.base_url))
    return {"success": True, "data": data}


@router.post("/{vm_id}/skills", status_code=202)
async def run_skill(vm_id: int, body: SkillRunBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """Corre una skill de Claude (`claude -p`) sobre un PR de una tarjeta en la VM. El cliente elige la skill del
    catálogo y un PR ya registrado en la tarjeta; no manda comandos. Se sigue con `GET /vms/tasks/{id}`."""
    data = await skill_service.start(db, actor, vm_id, body.skill, body.cardId, body.prUrl)
    return {"success": True, "data": data}


@router.post("/tasks/{task_id}/reply", status_code=202)
async def reply_skill(task_id: int, body: SkillReplyBody, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    """Responde a Claude en la conversación de una skill: un turno más en la misma sesión y el mismo log."""
    return {"success": True, "data": await skill_service.reply(db, actor, task_id, body.message)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, actor: CurrentActor, db: AsyncSession = Depends(get_db)):
    return {"success": True, "data": await vm_doctor_service.get_task(db, actor, task_id)}
