async def _board(client):
    return (await client.get("/api/board")).json()["data"]


async def _list_id(client, name):
    return next(lst["id"] for lst in (await _board(client))["lists"] if lst["name"] == name)


async def _new_card(client, list_id, **extra):
    """Crea una tarjeta válida (descripción + repo), creando el repo si falta."""
    await client.post("/api/repositories", json={"key": "helios", "name": "Helios"})
    resp = await client.post("/api/cards", json={
        "title": "x", "listId": list_id, "description": "desc", "repositories": ["helios"], **extra,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def test_requires_auth(client):
    assert (await client.get("/api/board")).status_code == 401


async def test_bad_login(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert resp.status_code == 401


async def test_default_lists(admin):
    names = [lst["name"] for lst in (await _board(admin))["lists"]]
    assert names == ["Pendiente", "En proceso", "PR", "Testing", "Hecho"]


async def test_card_lifecycle(admin):
    repo = await admin.post("/api/repositories", json={"key": "helios", "name": "Helios"})
    assert repo.status_code == 201, repo.text
    assert (await admin.post("/api/repositories", json={"key": "helios", "name": "X"})).status_code == 409

    pending = await _list_id(admin, "Pendiente")
    in_progress = await _list_id(admin, "En proceso")
    ids = []
    for title in ("a", "b", "c"):
        resp = await admin.post("/api/cards", json={
            "title": title, "listId": pending, "description": "d", "repositories": ["helios"],
        })
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["data"]["id"])

    # Insertar al principio reindexa el resto.
    first = await _new_card(admin, pending, title="z", position=0)
    titles = [c["title"] for c in (await _board(admin))["lists"][0]["cards"]]
    assert titles == ["z", "a", "b", "c"]

    moved = await admin.post(f"/api/cards/{ids[1]}/move", json={"listId": in_progress})
    assert moved.json()["data"]["listName"] == "En proceso"
    board = await _board(admin)
    assert [c["position"] for c in board["lists"][0]["cards"]] == [0, 1, 2]
    assert [c["title"] for c in board["lists"][1]["cards"]] == ["b"]

    upd = await admin.patch(f"/api/cards/{first['id']}", json={"description": "hola"})
    assert upd.json()["data"]["description"] == "hola"

    assert (await admin.post(f"/api/cards/{ids[0]}/comments", json={"body": "ok"})).status_code == 201
    detail = (await admin.get(f"/api/cards/{ids[1]}")).json()["data"]
    assert [a["action"] for a in detail["activity"]] == ["moved", "created"]
    assert detail["activity"][0]["data"] == {"from": "Pendiente", "to": "En proceso"}

    assert (await admin.post(f"/api/cards/{ids[0]}/archive")).status_code == 200
    titles = [c["title"] for c in (await _board(admin))["lists"][0]["cards"]]
    assert titles == ["z", "c"]
    archived = (await admin.get("/api/cards", params={"archived": True})).json()["data"]
    assert [c["id"] for c in archived] == [ids[0]]


async def test_unknown_repository_is_reported(admin):
    pending = await _list_id(admin, "Pendiente")
    resp = await admin.post("/api/cards", json={
        "title": "x", "listId": pending, "description": "d", "repositories": ["nope"],
    })
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


async def test_card_requires_description_and_repository(admin):
    await admin.post("/api/repositories", json={"key": "helios", "name": "Helios"})
    pending = await _list_id(admin, "Pendiente")
    resp = await admin.post("/api/cards", json={"title": "x", "listId": pending, "repositories": ["helios"]})
    assert resp.status_code == 400
    assert "descripción" in resp.json()["detail"]
    resp = await admin.post("/api/cards", json={
        "title": "x", "listId": pending, "description": "   ", "repositories": ["helios"],
    })
    assert resp.status_code == 400
    resp = await admin.post("/api/cards", json={"title": "x", "listId": pending, "description": "d"})
    assert resp.status_code == 400
    assert "repositorio" in resp.json()["detail"]


async def test_card_assignee_defaults_to_creator(admin):
    pending = await _list_id(admin, "Pendiente")
    me = (await admin.get("/api/auth/me")).json()["data"]
    assert (await _new_card(admin, pending))["assignee"]["id"] == me["id"]


async def test_delete_list_with_cards_is_rejected(admin):
    pending = await _list_id(admin, "Pendiente")
    await _new_card(admin, pending)
    assert (await admin.delete(f"/api/lists/{pending}")).status_code == 409


async def test_member_cannot_manage_users(admin):
    resp = await admin.post("/api/users", json={
        "username": "dev", "email": "dev@optidev.com", "name": "Dev", "password": "dev-pass-123",
    })
    assert resp.status_code == 201, resp.text
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    token = login.json()["data"]["token"]
    resp = await admin.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_dates_are_utc_aware(admin):
    pending = await _list_id(admin, "Pendiente")
    card = await _new_card(admin, pending)
    fetched = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert fetched["createdAt"].endswith("+00:00")
    assert fetched["activity"][0]["createdAt"].endswith("+00:00")


async def test_onboarding_flag(admin):
    assert (await admin.get("/api/auth/me")).json()["data"]["onboardingCompleted"] is False
    resp = await admin.post("/api/auth/onboarding/complete")
    assert resp.json()["data"]["onboardingCompleted"] is True
    assert (await admin.get("/api/auth/me")).json()["data"]["onboardingCompleted"] is True


async def test_dark_mode_is_per_user(admin):
    assert (await admin.get("/api/auth/me")).json()["data"]["darkMode"] is False
    resp = await admin.patch("/api/auth/me", json={"darkMode": True})
    assert resp.json()["data"]["darkMode"] is True
    # Cambiar otro dato del perfil no lo resetea.
    resp = await admin.patch("/api/auth/me", json={"name": "Admin"})
    assert resp.json()["data"]["darkMode"] is True

    await admin.post("/api/users", json={
        "username": "dev", "email": "dev@optidev.com", "name": "Dev", "password": "dev-pass-123",
    })
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    assert login.json()["data"]["user"]["darkMode"] is False


async def test_collapsible_list(admin):
    lists = (await _board(admin))["lists"]
    assert all(lst["collapsible"] is False for lst in lists)

    created = await admin.post("/api/lists", json={"name": "Backlog", "collapsible": True})
    assert created.status_code == 201, created.text
    backlog = created.json()["data"]
    assert backlog["collapsible"] is True

    # PUT es parcial: cambiar solo `collapsible` no toca el nombre, y viceversa.
    resp = await admin.put(f"/api/lists/{backlog['id']}", json={"collapsible": False})
    assert resp.json()["data"] == {**backlog, "collapsible": False}
    resp = await admin.put(f"/api/lists/{backlog['id']}", json={"name": "Ideas"})
    assert resp.json()["data"]["name"] == "Ideas"
    assert resp.json()["data"]["collapsible"] is False
    assert (await admin.put(f"/api/lists/{backlog['id']}", json={"name": "  "})).status_code == 422
    assert (await admin.put(f"/api/lists/{backlog['id']}", json={"name": ""})).status_code == 422

    await admin.put(f"/api/lists/{backlog['id']}", json={"collapsible": True})
    board_list = next(lst for lst in (await _board(admin))["lists"] if lst["id"] == backlog["id"])
    assert board_list["collapsible"] is True


async def test_list_color_is_admin_only(admin):
    pending = await _list_id(admin, "Pendiente")
    resp = await admin.put(f"/api/lists/{pending}/color", json={"color": "#FA8C16"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["color"] == "#fa8c16"
    board_list = next(lst for lst in (await _board(admin))["lists"] if lst["id"] == pending)
    assert board_list["color"] == "#fa8c16"
    assert (await admin.put(f"/api/lists/{pending}/color", json={"color": "rojo"})).status_code == 422
    resp = await admin.put(f"/api/lists/{pending}/color", json={"color": None})
    assert resp.json()["data"]["color"] is None

    await admin.post("/api/users", json={
        "username": "dev", "email": "dev@optidev.com", "name": "Dev", "password": "dev-pass-123",
    })
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    resp = await admin.put(f"/api/lists/{pending}/color", json={"color": "#f5222d"}, headers=member)
    assert resp.status_code == 403


async def test_list_reorder_is_admin_only(admin):
    ids = [lst["id"] for lst in (await _board(admin))["lists"]]
    reversed_ids = list(reversed(ids))

    await admin.post("/api/users", json={
        "username": "dev", "email": "dev@optidev.com", "name": "Dev", "password": "dev-pass-123",
    })
    login = await admin.post("/api/auth/login", json={"username": "dev", "password": "dev-pass-123"})
    member = {"Authorization": f"Bearer {login.json()['data']['token']}"}
    resp = await admin.post("/api/lists/reorder", json={"ids": reversed_ids}, headers=member)
    assert resp.status_code == 403
    assert [lst["id"] for lst in (await _board(admin))["lists"]] == ids

    resp = await admin.post("/api/lists/reorder", json={"ids": reversed_ids})
    assert resp.status_code == 200, resp.text
    assert [lst["id"] for lst in (await _board(admin))["lists"]] == reversed_ids


async def test_my_activity(admin):
    pending = await _list_id(admin, "Pendiente")
    in_progress = await _list_id(admin, "En proceso")
    card = await _new_card(admin, pending, title="historial")
    await admin.post(f"/api/cards/{card['id']}/move", json={"listId": in_progress})
    await admin.post(f"/api/cards/{card['id']}/comments", json={"body": "hola"})
    await admin.post(f"/api/cards/{card['id']}/archive")

    items = (await admin.get("/api/auth/me/activity")).json()["data"]
    assert [a["action"] for a in items] == ["archived", "commented", "moved", "created"]
    assert items[0]["card"] == {"id": card["id"], "title": "historial"}
    assert items[2]["data"] == {"from": "Pendiente", "to": "En proceso"}

    # Paginado por id: lo siguiente a la última entrada mostrada.
    page = (await admin.get("/api/auth/me/activity", params={"limit": 2})).json()["data"]
    assert [a["action"] for a in page] == ["archived", "commented"]
    rest = (await admin.get("/api/auth/me/activity", params={"before": page[-1]["id"]})).json()["data"]
    assert [a["action"] for a in rest] == ["moved", "created"]

    # Solo lo propio: otro usuario no ve la actividad del admin.
    await admin.post("/api/users", json={
        "username": "otro", "email": "otro@x.cl", "name": "Otro", "password": "pass-otro-123", "role": "member",
    })
    login = await admin.post("/api/auth/login", json={"username": "otro", "password": "pass-otro-123"})
    token = login.json()["data"]["token"]
    resp = await admin.get("/api/auth/me/activity", headers={"Authorization": f"Bearer {token}"})
    assert resp.json()["data"] == []
