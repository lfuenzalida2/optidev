VM = {"name": "VM de ana", "host": "10.0.0.5", "username": "qa", "authType": "password", "password": "secret"}


async def _create_user(admin, username, role="member"):
    resp = await admin.post("/api/users", json={
        "username": username, "email": f"{username}@optidev.com", "name": username.title(),
        "password": "pass-12345", "role": role,
    })
    assert resp.status_code == 201, resp.text
    login = await admin.post("/api/auth/login", json={"username": username, "password": "pass-12345"})
    return resp.json()["data"]["id"], {"Authorization": f"Bearer {login.json()['data']['token']}"}


async def _notifications(admin, headers):
    return (await admin.get("/api/notifications", headers=headers)).json()["data"]


async def test_admin_creates_vm_for_user_and_closes_the_request(admin):
    ana_id, ana = await _create_user(admin, "ana")
    await admin.post("/api/vms/request", headers=ana)
    assert [r["user"]["username"] for r in (await admin.get("/api/vms/requests")).json()["data"]] == ["ana"]

    resp = await admin.post("/api/vms", json={**VM, "userId": ana_id})
    assert resp.status_code == 201, resp.text

    # La VM es de ana (la ve en su perfil), y la solicitud se cerró.
    assert [vm["name"] for vm in (await admin.get("/api/vms", headers=ana)).json()["data"]] == ["VM de ana"]
    assert (await admin.get("/api/vms")).json()["data"] == []
    assert (await admin.get("/api/vms/requests")).json()["data"] == []
    assert (await admin.get("/api/vms/request", headers=ana)).json()["data"] is None
    # Se le avisa a ana, y puede volver a pedir otra.
    notification = (await _notifications(admin, ana))["items"][0]
    assert notification["kind"] == "vm_assigned" and notification["data"] == {"vmName": "VM de ana"}
    assert (await admin.post("/api/vms/request", headers=ana)).status_code == 201


async def test_creating_own_vm_closes_own_request_without_notifying(admin):
    _, ana = await _create_user(admin, "ana")
    await admin.post("/api/vms/request", headers=ana)
    assert (await admin.post("/api/vms", json=VM, headers=ana)).status_code == 201
    assert (await admin.get("/api/vms/request", headers=ana)).json()["data"] is None
    assert (await _notifications(admin, ana))["items"] == []


async def test_only_admins_create_vms_for_others(admin):
    ana_id, ana = await _create_user(admin, "ana")
    beto_id, _ = await _create_user(admin, "beto")
    assert (await admin.post("/api/vms", json={**VM, "userId": beto_id}, headers=ana)).status_code == 403
    assert (await admin.post("/api/vms", json={**VM, "userId": 9999})).status_code == 404
    # Pasar el propio id no es "para otro".
    assert (await admin.post("/api/vms", json={**VM, "userId": ana_id}, headers=ana)).status_code == 201


async def test_all_vms_and_requests_are_admin_only(admin):
    ana_id, ana = await _create_user(admin, "ana")
    await admin.post("/api/vms", json={**VM, "userId": ana_id})
    listed = (await admin.get("/api/vms/all")).json()["data"]
    assert [(vm["name"], vm["owner"]["username"]) for vm in listed] == [("VM de ana", "ana")]
    assert "secret" not in str(listed)
    assert (await admin.get("/api/vms/all", headers=ana)).status_code == 403
    assert (await admin.get("/api/vms/requests", headers=ana)).status_code == 403


async def test_admin_manages_any_vm_but_users_only_their_own(admin):
    ana_id, ana = await _create_user(admin, "ana")
    _, beto = await _create_user(admin, "beto")
    vm_id = (await admin.post("/api/vms", json={**VM, "userId": ana_id})).json()["data"]["id"]

    assert (await admin.patch(f"/api/vms/{vm_id}", json={"name": "Renombrada"})).json()["data"]["name"] == "Renombrada"
    assert (await admin.patch(f"/api/vms/{vm_id}", json={"name": "x"}, headers=beto)).status_code == 404
    assert (await admin.delete(f"/api/vms/{vm_id}", headers=beto)).status_code == 404
    assert (await admin.delete(f"/api/vms/{vm_id}")).status_code == 200
    assert (await admin.get("/api/vms", headers=ana)).json()["data"] == []


async def test_admin_cannot_run_owner_only_actions_on_another_users_vm(admin):
    """Diagnóstico, arreglos y skills instalan un token a nombre del actor en la VM: en una VM ajena
    sería el token del admin, así que siguen siendo solo del dueño."""
    ana_id, ana = await _create_user(admin, "ana")
    vm_id = (await admin.post("/api/vms", json={**VM, "userId": ana_id})).json()["data"]["id"]

    assert (await admin.post(f"/api/vms/{vm_id}/diagnose")).status_code == 404
    assert (await admin.post(f"/api/vms/{vm_id}/fixes/anything")).status_code == 404
    assert (await admin.post(f"/api/vms/{vm_id}/skills", json={"skill": "revisar-pr", "cardId": 1, "prUrl": "x"})).status_code == 404
