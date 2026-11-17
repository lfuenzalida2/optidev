import asyncio
import re
import subprocess

from src.services import skill_service, vm_doctor_service
from src.services.vm_doctor_service import Issue
from tests.test_board import _list_id, _new_card
from tests.test_previews import _load, _setup, origin, vm_port  # noqa: F401 — fixtures


async def _vm(admin, port):
    return (await admin.post("/api/vms", json={"host": "127.0.0.1", "port": port, "username": "qa",
                                               "authType": "password", "password": "secret"})).json()["data"]


async def _wait(admin, task):
    for _ in range(200):
        task = (await admin.get(f"/api/vms/tasks/{task['id']}")).json()["data"]
        if task["status"] != "running":
            return task
        await asyncio.sleep(0.05)
    raise AssertionError(task["log"])


def test_catalog_commands_are_valid_shell():
    # Un error de sintaxis acá recién se vería al apretar "Resolver" en una VM.
    for issue in vm_doctor_service._issues():
        for command in (issue.check, issue.fix):
            if command:
                script = f"{vm_doctor_service._ROOT}\nset -e\n{command}"
                result = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
                assert result.returncode == 0, (issue.id, result.stderr)


def test_issues_in_log():
    found = vm_doctor_service.issues_in_log("bash: line 7: docker: command not found\n")
    assert [i["id"] for i in found] == ["docker"] and found[0]["fixTitle"] == "Instalar Docker"
    assert [i["id"] for i in vm_doctor_service.issues_in_log("Cannot connect to the Docker daemon at unix://")] == [
        "docker_daemon"]
    assert vm_doctor_service.issues_in_log("todo bien") == []


async def test_agents_group(admin, vm_port):
    # El marketplace de agentes es una herramienta aparte: no sale como problema de la VM.
    vm = await _vm(admin, vm_port)
    data = (await admin.post(f"/api/vms/{vm['id']}/diagnose", params={"group": "agents"})).json()["data"]
    assert [c["id"] for c in data["checks"]] == ["agents_marketplace", "agents_current", "agents_timer"]
    assert all(c["fixTitle"] for c in data["checks"])
    assert vm_doctor_service.issues_in_log("agents_marketplace") == []
    # Mismo script a mano y de noche; instala/actualiza cada plugin que declara el marketplace.json.
    script = vm_doctor_service.AGENTS_UPDATE_SCRIPT
    assert f"marketplace add {vm_doctor_service.AGENTS_REPO}" in script
    assert f"marketplace update {vm_doctor_service.AGENTS_MARKETPLACE}" in script
    assert "plugin install" in script and "plugin update" in script
    fix = next(i for i in vm_doctor_service._issues() if i.id == "agents_marketplace").fix
    assert script in fix
    timer = next(i for i in vm_doctor_service._issues() if i.id == "agents_timer").fix
    assert vm_doctor_service.AGENTS_UPDATE_SCHEDULE in timer and "Persistent=true" in timer


async def test_diagnose_runs_the_catalog(admin, vm_port):
    vm = await _vm(admin, vm_port)
    data = (await admin.post(f"/api/vms/{vm['id']}/diagnose")).json()["data"]
    checks = {c["id"]: c for c in data["checks"]}
    assert set(checks) == {i.id for i in vm_doctor_service._issues() if i.group == "doctor"}
    assert checks["git"]["ok"] is True  # la "VM" de los tests es esta máquina, que tiene git
    assert all(isinstance(c["ok"], bool) for c in checks.values())


async def test_claude_rc_group(admin, vm_port):
    # Los pasos de Remote Control son una herramienta aparte: no salen como problemas de la VM.
    vm = await _vm(admin, vm_port)
    data = (await admin.post(f"/api/vms/{vm['id']}/diagnose", params={"group": "claude_rc"})).json()["data"]
    assert [c["id"] for c in data["checks"]] == ["claude", "claude_login", "claude_rc"]
    login = data["checks"][1]
    assert login["hint"] and not login["fixTitle"]  # el login es a mano, en la terminal
    # La carpeta lleva el nombre de la VM: Claude Code agrupa las sesiones por ella.
    assert login["command"] == f"cd {vm_doctor_service.claude_dir_for(vm['name'], vm['id'])} && ~/.local/bin/claude"
    assert vm_doctor_service.issues_in_log("claude: command not found") == []
    assert (await admin.post(f"/api/vms/{vm['id']}/diagnose", params={"group": "otro"})).status_code == 404


def test_claude_dir():
    assert vm_doctor_service.claude_dir_for("Test1", 2) == "/opt/claude/test1"
    assert vm_doctor_service.claude_dir_for("Mi VM de pruebas (Lukas)", 1) == "/opt/claude/mi-vm-de-pruebas-lukas"
    assert vm_doctor_service.claude_dir_for("Ñandú", 3) == "/opt/claude/nandu"
    assert vm_doctor_service.claude_dir_for("¡¡!!", 7) == "/opt/claude/vm-7"


def test_claude_rc_unit(tmp_path):
    # El heredoc del arreglo arma la unidad con la carpeta de la VM, el usuario y su HOME.
    fix = next(i for i in vm_doctor_service._issues() if i.id == "claude_rc").fix
    start = fix.index("$S tee")
    heredoc = fix[start:fix.index("\nEOF", start) + 4].replace(
        "$S tee /etc/systemd/system/claude-rc.service >/dev/null", "cat")

    def unit(sandbox_env=""):
        return subprocess.run(["sh", "-c", f"CLAUDE_DIR=/opt/claude/test1\nSANDBOX_ENV='{sandbox_env}'\n{heredoc}"],
                              capture_output=True, text=True, env={"HOME": "/home/qa", "PATH": "/usr/bin:/bin"}).stdout

    normal = unit()
    assert "WorkingDirectory=/opt/claude/test1\n" in normal
    assert "ExecStart=/home/qa/.local/bin/claude remote-control --permission-mode bypassPermissions" in normal
    assert "Restart=always" in normal and "StandardOutput=null" in normal
    assert "IS_SANDBOX" not in normal
    # Como root, Claude solo acepta bypassPermissions con IS_SANDBOX=1.
    assert "Environment=IS_SANDBOX=1\n" in unit("Environment=IS_SANDBOX=1")


async def test_fix_flow(admin, vm_port, tmp_path, monkeypatch):
    flag = tmp_path / "flag"
    catalog = [
        Issue("flag", "Falta el archivo", f"test -f {flag}", "Crear el archivo", f"echo creando; touch {flag}"),
        Issue("roto", "Arreglo que falla", "false", "Romper", "echo intento; exit 5"),
        Issue("inutil", "Arreglo que no arregla", "false", "No hacer nada", "true"),
        Issue("manual", "Sin arreglo automático", "false"),
    ]
    monkeypatch.setattr(vm_doctor_service, "_issues", lambda: catalog)
    vm = await _vm(admin, vm_port)

    checks = {c["id"]: c["ok"] for c in (await admin.post(f"/api/vms/{vm['id']}/diagnose")).json()["data"]["checks"]}
    assert checks == {"flag": False, "roto": False, "inutil": False, "manual": False}

    task = await _wait(admin, (await admin.post(f"/api/vms/{vm['id']}/fixes/flag")).json()["data"])
    assert task["status"] == "success" and "creando" in task["log"] and flag.exists()
    checks = {c["id"]: c["ok"] for c in (await admin.post(f"/api/vms/{vm['id']}/diagnose")).json()["data"]["checks"]}
    assert checks["flag"] is True

    task = await _wait(admin, (await admin.post(f"/api/vms/{vm['id']}/fixes/roto")).json()["data"])
    assert task["status"] == "failed" and "intento" in task["log"] and "código 5" in task["log"]
    task = await _wait(admin, (await admin.post(f"/api/vms/{vm['id']}/fixes/inutil")).json()["data"])
    assert task["status"] == "failed" and "sigue" in task["log"]

    # El cliente solo elige del catálogo.
    assert (await admin.post(f"/api/vms/{vm['id']}/fixes/manual")).status_code == 400
    assert (await admin.post(f"/api/vms/{vm['id']}/fixes/rm -rf")).status_code == 404


async def test_failed_load_suggests_fix(admin, vm_port, origin):  # noqa: F811
    _, vm = await _setup(admin, vm_port, recipe="echo 'docker: command not found' >&2\nexit 127\n")
    load = await _load(admin, vm)
    assert load["status"] == "failed"
    assert [i["id"] for i in load["issues"]] == ["docker"]


async def test_other_users_vm(admin, vm_port):
    vm = await _vm(admin, vm_port)
    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    assert (await admin.post(f"/api/vms/{vm['id']}/diagnose", headers=member)).status_code == 404
    assert (await admin.post(f"/api/vms/{vm['id']}/fixes/git", headers=member)).status_code == 404


async def test_self_is_blocked_for_changes(admin, vm_port, monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings, "PREVIEW_ALLOW_SELF", False)
    vm = await _vm(admin, vm_port)  # 127.0.0.1 = esta máquina
    resp = await admin.post(f"/api/vms/{vm['id']}/fixes/git")
    assert resp.status_code == 400 and "servidor de optidev" in resp.json()["detail"]
    resp = await admin.post("/api/previews", json={"repository": "x", "branch": "b", "vmId": vm["id"]})
    assert resp.status_code in (400, 404)
    # Diagnosticar no modifica nada: se permite.
    assert (await admin.post(f"/api/vms/{vm['id']}/diagnose")).status_code == 200
    assert vm_doctor_service.vm_service._is_local_address("127.0.0.1")
    assert not vm_doctor_service.vm_service._is_local_address("192.0.2.1")  # TEST-NET, nunca local


async def test_routine_group(admin, vm_port):
    vm = await _vm(admin, vm_port)
    data = (await admin.post(f"/api/vms/{vm['id']}/diagnose", params={"group": "routine"})).json()["data"]
    assert [c["id"] for c in data["checks"]] == ["routine_mcp", "routine"]
    assert all(c["fixTitle"] for c in data["checks"])


def _heredoc(fix: str, target: str) -> str:
    start = fix.index(f"$S tee {target}")
    return fix[start:fix.index("\nEOF", start) + 4].replace(f"$S tee {target} >/dev/null", "cat")


def test_routine_units():
    fix = next(i for i in vm_doctor_service._issues() if i.id == "routine").fix
    env = {"HOME": "/home/qa", "PATH": "/usr/bin:/bin"}
    script = "CLAUDE_DIR=/opt/claude/test1\nSANDBOX_ENV='Environment=IS_SANDBOX=1'\n"
    service = subprocess.run(["sh", "-c", script + _heredoc(fix, "/etc/systemd/system/optidev-rutina.service")],
                             capture_output=True, text=True, env=env).stdout
    assert "WorkingDirectory=/opt/claude/test1\n" in service and "Environment=IS_SANDBOX=1\n" in service
    # systemd convierte `$$` en `$`: el sh de ExecStart lee el prompt del archivo.
    assert (f"ExecStart=/bin/sh -c 'exec claude -p --dangerously-skip-permissions "
            f"\"$$(cat {vm_doctor_service.ROUTINE_PROMPT_FILE})\"'") in service
    timer = subprocess.run(["sh", "-c", script + _heredoc(fix, "/etc/systemd/system/optidev-rutina.timer")],
                           capture_output=True, text=True, env=env).stdout
    assert f"OnCalendar={vm_doctor_service.ROUTINE_SCHEDULE}\n" in timer
    # El prompt es el que llama a report_routine_run (lo que marca la rutina como configurada).
    assert "report_routine_run" in vm_doctor_service.ROUTINE_PROMPT


async def test_fix_with_mcp_token(admin, vm_port, tmp_path, monkeypatch):
    out = tmp_path / "mcp"
    catalog = [Issue("mcp", "MCP", f"test -s {out}", "Conectar",
                     f'echo "url=$ODV_URL token=$ODV_TOKEN"; printf %s "$ODV_TOKEN" > {out}',
                     group="routine", mcp_token=True)]
    monkeypatch.setattr(vm_doctor_service, "_issues", lambda: catalog)
    vm = await _vm(admin, vm_port)

    async def run():
        task = await _wait(admin, (await admin.post(f"/api/vms/{vm['id']}/fixes/mcp")).json()["data"])
        assert task["status"] == "success", task["log"]
        return task, out.read_text()

    task, first = await run()
    assert first.startswith("odv_") and first not in task["log"] and "token=***" in task["log"]
    assert "url=http://testserver token" in task["log"]  # sin "/" final
    # El token sirve contra la API como el usuario que instaló.
    me = await admin.get("/api/auth/me", headers={"Authorization": f"Bearer {first}"})
    assert me.status_code == 200 and me.json()["data"]["username"] == "admin"
    tokens = (await admin.get("/api/tokens")).json()["data"]
    assert [t["name"] for t in tokens] == [vm_doctor_service.ROUTINE_TOKEN_NAME.format(vm=vm["name"])]

    # Reinstalar revoca el token anterior de la misma VM.
    _, second = await run()
    assert second != first
    assert (await admin.get("/api/auth/me", headers={"Authorization": f"Bearer {first}"})).status_code == 401
    assert len((await admin.get("/api/tokens")).json()["data"]) == 1


async def test_routine_mcp_check(admin, vm_port, tmp_path, monkeypatch):
    # El chequeo exige este optidev y el token vigente de la VM: un MCP de otra instancia (prod vs. preview)
    # registraría las corridas en otro lado.
    monkeypatch.setenv("HOME", str(tmp_path))
    vm = await _vm(admin, vm_port)
    issue = next(i for i in vm_doctor_service._issues() if i.id == "routine_mcp")

    def check(url, prefix, server):
        (tmp_path / ".claude.json").write_text(__import__("json").dumps({"mcpServers": {"optidev": server}}))
        script = issue.check.split(" && ")[0]  # sin `claude mcp get` (no hay Claude en los tests)
        env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "ODV_URL": url, "ODV_PREFIX": prefix}
        return subprocess.run(["sh", "-c", script], env=env).returncode == 0

    server = {"url": "http://testserver/mcp", "headers": {"Authorization": "Bearer odv_abc123xyz"}}
    assert check("http://testserver", "odv_abc123", server)
    assert not check("http://otro", "odv_abc123", server)
    assert not check("http://testserver", "odv_zzz999", server)
    assert not check("http://testserver", "", server)  # sin token vigente para la VM

    # El diagnóstico manda la URL de la petición y el prefijo del token de la rutina de esa VM.
    seen = {}
    real = vm_doctor_service._routine_env

    async def spy(*args, **kwargs):
        seen.update(await real(*args, **kwargs))
        return seen
    monkeypatch.setattr(vm_doctor_service, "_routine_env", spy)
    await admin.post(f"/api/vms/{vm['id']}/diagnose", params={"group": "routine"})
    assert seen == {"ODV_URL": "http://testserver", "ODV_PREFIX": ""}


# --- Skills de Claude (claude -p) sobre un PR -------------------------------------------

PR = "https://github.com/org/demo/pull/3"


async def _card(admin, pr_urls=(PR,)):
    card = await _new_card(admin, await _list_id(admin, "Testing"))
    await admin.patch(f"/api/cards/{card['id']}", json={"prUrls": list(pr_urls)})
    return card


def _fake_claude(tmp_path, monkeypatch, body='echo "claude $*"', credentials=True):
    """`claude` falso en el HOME de la "VM" (esta máquina): escribe sus argumentos."""
    monkeypatch.setenv("HOME", str(tmp_path))
    claude = tmp_path / ".local" / "bin" / "claude"
    claude.parent.mkdir(parents=True)
    claude.write_text(f"#!/bin/sh\n{body}\n")
    claude.chmod(0o755)
    if credentials:
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / ".credentials.json").write_text("{}")


async def _skill(admin, vm, card, url=PR, skill="revisar-pr"):
    return await admin.post(f"/api/vms/{vm['id']}/skills", json={"skill": skill, "cardId": card["id"], "prUrl": url})


async def test_run_skill_on_pr(admin, vm_port, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch)
    vm = await _vm(admin, vm_port)
    card = await _card(admin)

    resp = await _skill(admin, vm, card)
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"]["title"] == "Skill revisar-pr"
    task = await _wait(admin, resp.json()["data"])
    assert task["status"] == "success", task["log"]
    # `claude -p` sin pedir permisos, con el prompt armado por el servidor.
    assert "claude -p --dangerously-skip-permissions Usá la skill revisar-pr" in task["log"]
    assert f"sobre este pull request: {PR}" in task["log"] and "No mergees" in task["log"]
    # Queda registrado en la tarjeta.
    comments = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]["comments"]
    assert "revisar-pr" in comments[-1]["body"] and vm["name"] in comments[-1]["body"]


async def test_run_skill_failures_are_reported(admin, vm_port, tmp_path, monkeypatch):
    vm = await _vm(admin, vm_port)
    card = await _card(admin)

    _fake_claude(tmp_path / "sin-login", monkeypatch, credentials=False)
    task = await _wait(admin, (await _skill(admin, vm, card)).json()["data"])
    assert task["status"] == "failed" and "Falta iniciar sesión en Claude" in task["log"]

    _fake_claude(tmp_path / "falla", monkeypatch, body="echo 'sin permiso a gh' >&2; exit 3")
    task = await _wait(admin, (await _skill(admin, vm, card)).json()["data"])
    assert task["status"] == "failed" and "sin permiso a gh" in task["log"] and "código 3" in task["log"]

    monkeypatch.setenv("HOME", str(tmp_path / "vacio"))
    (tmp_path / "vacio").mkdir()
    task = await _wait(admin, (await _skill(admin, vm, card)).json()["data"])
    assert task["status"] == "failed" and "Claude Code no está instalado" in task["log"]


async def test_run_skill_only_catalog_and_registered_prs(admin, vm_port, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch)
    vm = await _vm(admin, vm_port)
    card = await _card(admin)
    other = await _card(admin, pr_urls=())

    assert (await _skill(admin, vm, card, skill="rm -rf /")).status_code == 400
    # PR que no está en la tarjeta, o un link que no es de un PR (nada de comandos por acá).
    assert (await _skill(admin, vm, other)).status_code == 400
    for url in ["https://github.com/org/demo/pull/9", "x; rm -rf /", "https://evil.test/org/demo/pull/3"]:
        assert (await _skill(admin, vm, card, url=url)).status_code == 400
    assert (await _skill(admin, vm, {"id": 9999})).status_code == 404
    assert (await admin.post(f"/api/vms/{vm['id']}/skills", json={"skill": "revisar-pr"})).status_code == 422
    # Nada de eso llegó a la VM.
    tasks = [t for t in range(1, 4) if (await admin.get(f"/api/vms/tasks/{t}")).status_code == 200]
    assert tasks == []


def _pr_branches(monkeypatch, head, base, state="open"):
    async def rest(method, path, **kwargs):
        return 200, {"state": state, "head": {"ref": head}, "base": {"ref": base}}
    monkeypatch.setattr(skill_service.github_service, "rest", rest)


async def test_release_skill_only_on_open_staging_to_master_pr(admin, vm_port, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch)
    vm = await _vm(admin, vm_port)
    card = await _card(admin)

    _pr_branches(monkeypatch, "feat/x", "staging")
    resp = await _skill(admin, vm, card, skill="release")
    assert resp.status_code == 400 and "staging → master" in resp.json()["detail"]
    _pr_branches(monkeypatch, "staging", "master", state="closed")
    assert (await _skill(admin, vm, card, skill="release")).status_code == 400

    _pr_branches(monkeypatch, "staging", "master")
    resp = await _skill(admin, vm, card, skill="release")
    assert resp.status_code == 202, resp.text
    task = await _wait(admin, resp.json()["data"])
    assert task["status"] == "success", task["log"]
    # Los 4 pasos, en orden, con el commit exacto; sin permiso para mergear.
    log = task["log"]
    steps = ["Subí la versión", "service-requirements-scanner", "requirements-notes-resolver", 'mensaje exacto']
    assert [log.index(x) for x in steps] == sorted(log.index(x) for x in steps)
    assert PR in log and "shared/" in log and "No mergees" in log


async def test_run_skill_one_task_per_vm_and_own_vm_only(admin, vm_port, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, body="sleep 1; echo listo")
    vm = await _vm(admin, vm_port)
    card = await _card(admin)
    first = await _skill(admin, vm, card)
    assert first.status_code == 202
    assert (await _skill(admin, vm, card)).status_code == 409  # ya hay una tarea corriendo en esa VM
    await _wait(admin, first.json()["data"])

    await admin.post("/api/users", json={"username": "dev", "email": "dev@optidev.com", "name": "Dev",
                                         "password": "dev-pass-123"})
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    resp = await admin.post(f"/api/vms/{vm['id']}/skills", headers=member,
                            json={"skill": "revisar-pr", "cardId": card["id"], "prUrl": PR})
    assert resp.status_code == 404


async def test_skill_conversation_resumes_the_session(admin, vm_port, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch)
    vm = await _vm(admin, vm_port)
    card = await _card(admin)
    first = await _wait(admin, (await _skill(admin, vm, card)).json()["data"])
    # La primera corrida abre una sesión con id propio y se puede responder.
    session = re.search(r"--session-id ([0-9a-f-]{36})", first["log"]).group(1)
    assert first["canReply"] is True

    resp = await admin.post(f"/api/vms/tasks/{first['id']}/reply", json={"message": "Sí, subí los cambios; y no mergees"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"]["canReply"] is False  # corriendo
    task = await _wait(admin, resp.json()["data"])
    # Mismo log (el primer turno sigue ahí), turno nuevo con --resume sobre la misma sesión.
    assert task["status"] == "success" and "Usá la skill revisar-pr" in task["log"]
    assert "▶ Vos: Sí, subí los cambios; y no mergees" in task["log"]
    assert f"claude -p --dangerously-skip-permissions Sí, subí los cambios; y no mergees --resume {session}" in task["log"]
    assert task["canReply"] is True


async def test_skill_reply_validations(admin, vm_port, tmp_path, monkeypatch):
    _fake_claude(tmp_path, monkeypatch, body="sleep 1; echo listo")
    vm = await _vm(admin, vm_port)
    card = await _card(admin)
    running = (await _skill(admin, vm, card)).json()["data"]
    reply = lambda tid, msg: admin.post(f"/api/vms/tasks/{tid}/reply", json={"message": msg})  # noqa: E731
    assert (await reply(running["id"], "hola")).status_code == 409  # todavía corre
    await _wait(admin, running)
    assert (await reply(running["id"], "   ")).status_code == 400
    assert (await reply(running["id"], "x" * 4001)).status_code == 400
    assert (await reply(9999, "hola")).status_code == 404
