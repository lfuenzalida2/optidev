async def _login(admin, username, password="pass-12345"):
    resp = await admin.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {resp.json()['data']['token']}"}


async def _create_user(admin, username, role="member"):
    resp = await admin.post("/api/users", json={
        "username": username, "email": f"{username}@optidev.com", "name": username.title(),
        "password": "pass-12345", "role": role,
    })
    assert resp.status_code == 201, resp.text
    return await _login(admin, username)


async def _notifications(admin, headers):
    return (await admin.get("/api/notifications", headers=headers)).json()["data"]


async def test_vm_request_notifies_every_admin(admin):
    second_admin = await _create_user(admin, "jefa", role="admin")
    member = await _create_user(admin, "ana")

    resp = await admin.post("/api/vms/request", headers=member)
    assert resp.status_code == 201, resp.text

    for headers in ({"Authorization": admin.headers["Authorization"]}, second_admin):
        data = await _notifications(admin, headers)
        assert data["unreadCount"] == 1
        assert data["items"][0]["kind"] == "vm_requested"
        assert data["items"][0]["data"]["username"] == "ana"
    # Quien pide no recibe nada: el aviso es para los admins.
    assert (await _notifications(admin, member))["items"] == []


async def test_only_one_open_request_per_user(admin):
    member = await _create_user(admin, "ana")
    assert (await admin.get("/api/vms/request", headers=member)).json()["data"] is None

    assert (await admin.post("/api/vms/request", headers=member)).status_code == 201
    assert (await admin.post("/api/vms/request", headers=member)).status_code == 409
    assert (await admin.get("/api/vms/request", headers=member)).json()["data"]["id"]
    # El segundo intento no generó otro aviso.
    assert (await _notifications(admin, {"Authorization": admin.headers["Authorization"]}))["unreadCount"] == 1


async def test_admins_cannot_request_a_vm(admin):
    assert (await admin.post("/api/vms/request")).status_code == 400


async def test_mark_read_is_per_user(admin):
    second_admin = await _create_user(admin, "jefa", role="admin")
    member = await _create_user(admin, "ana")
    await admin.post("/api/vms/request", headers=member)
    first = {"Authorization": admin.headers["Authorization"]}

    notification_id = (await _notifications(admin, first))["items"][0]["id"]
    # La notificación de otro usuario se informa como inexistente.
    assert (await admin.post(f"/api/notifications/{notification_id}/read", headers=member)).status_code == 404
    assert (await admin.post(f"/api/notifications/{notification_id}/read", headers=first)).status_code == 200

    assert (await _notifications(admin, first))["unreadCount"] == 0
    assert (await _notifications(admin, second_admin))["unreadCount"] == 1

    assert (await admin.post("/api/notifications/read-all", headers=second_admin)).status_code == 200
    assert (await _notifications(admin, second_admin))["unreadCount"] == 0


async def test_disabled_channel_still_records_the_request(admin):
    member = await _create_user(admin, "ana")
    resp = await admin.put("/api/notifications/channels", json={"inApp": False})
    assert resp.json()["data"] == {"inApp": False}

    assert (await admin.post("/api/vms/request", headers=member)).status_code == 201
    first = {"Authorization": admin.headers["Authorization"]}
    assert (await _notifications(admin, first))["items"] == []
    assert (await admin.get("/api/vms/request", headers=member)).json()["data"] is not None

    assert (await admin.put("/api/notifications/channels", json={"inApp": True})).json()["data"] == {"inApp": True}


async def test_channels_are_admin_only(admin):
    member = await _create_user(admin, "ana")
    assert (await admin.get("/api/notifications/channels")).json()["data"] == {"inApp": True}
    assert (await admin.get("/api/notifications/channels", headers=member)).status_code == 403
    assert (await admin.put("/api/notifications/channels", json={"inApp": False}, headers=member)).status_code == 403
