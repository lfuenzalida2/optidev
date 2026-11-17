import json

_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def _token(admin):
    resp = await admin.post("/api/tokens", json={"name": "claude"})
    assert resp.status_code == 201
    return resp.json()["data"]["token"]


async def _call(client, token, tool, args=None):
    resp = await client.post("/mcp", headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args or {}},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _payload(result):
    if result.get("structuredContent") is not None:
        content = result["structuredContent"]
        return content.get("result", content)
    return json.loads(result["content"][0]["text"])


async def test_mcp_rejects_missing_and_jwt_tokens(admin):
    resp = await admin.post("/mcp", headers={**_MCP_HEADERS, "Authorization": ""}, json={})
    assert resp.status_code == 401
    # El JWT de la UI no sirve para el MCP: solo tokens personales.
    resp = await admin.post("/mcp", headers=_MCP_HEADERS, json={})
    assert resp.status_code == 401


async def test_mcp_revoked_token(admin):
    token = await _token(admin)
    token_id = (await admin.get("/api/tokens")).json()["data"][0]["id"]
    assert (await admin.delete(f"/api/tokens/{token_id}")).status_code == 200
    resp = await admin.post("/mcp", headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"}, json={})
    assert resp.status_code == 401


async def test_mcp_tools_list(admin):
    token = await _token(admin)
    resp = await admin.post("/mcp", headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    })
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert {"list_cards", "get_card", "move_card", "add_comment", "add_pr_url", "create_card", "get_repo_docs"} <= names


async def test_mcp_workflow(admin):
    await admin.post("/api/repositories", json={"key": "helios", "name": "Helios", "localPath": "/root/x"})
    token = await _token(admin)

    err = await _call(admin, token, "create_card", {"title": "Fix", "description": " ", "repositories": ["helios"]})
    assert err["isError"] is True

    card = _payload(await _call(admin, token, "create_card", {
        "title": "Fix", "description": "Arreglar", "repositories": ["helios"],
    }))
    assert card["list"] == "Pendiente"
    assert card["repositories"] == ["helios"]
    assert card["assignee"] == "admin"

    listed = _payload(await _call(admin, token, "list_cards", {"list": "pendiente", "repository": "helios"}))
    assert [c["id"] for c in listed] == [card["id"]]

    moved = _payload(await _call(admin, token, "move_card", {"card_id": card["id"], "list": "En proceso"}))
    assert moved["list"] == "En proceso"
    await _call(admin, token, "add_pr_url", {"card_id": card["id"], "url": "https://github.com/x/y/pull/1"})
    await _call(admin, token, "add_comment", {"card_id": card["id"], "body": "PR abierto"})

    detail = _payload(await _call(admin, token, "get_card", {"card_id": card["id"]}))
    assert detail["prUrls"] == ["https://github.com/x/y/pull/1"]
    assert detail["comments"][0]["via"] == "mcp"
    assert {a["via"] for a in detail["activity"]} == {"mcp"}

    err = await _call(admin, token, "move_card", {"card_id": card["id"], "list": "No existe"})
    assert err["isError"] is True
    assert "Listas disponibles" in err["content"][0]["text"]


async def test_mcp_lists_cards_of_collapsible_list(admin):
    # Ocultar una lista es solo visual: el MCP sigue viendo sus tarjetas.
    await admin.post("/api/repositories", json={"key": "helios", "name": "Helios"})
    backlog = (await admin.post("/api/lists", json={"name": "Backlog", "collapsible": True})).json()["data"]
    card = (await admin.post("/api/cards", json={
        "title": "Algún día", "listId": backlog["id"], "description": "d", "repositories": ["helios"],
    })).json()["data"]
    token = await _token(admin)

    cards = _payload(await _call(admin, token, "list_cards", {"list": "Backlog"}))
    assert [c["id"] for c in cards] == [card["id"]]
    info = _payload(await _call(admin, token, "get_board_info"))
    assert {"id": backlog["id"], "name": "Backlog", "cardCount": 1} in info["lists"]


async def test_mcp_report_routine_run(admin):
    token = await _token(admin)
    assert (await admin.get("/api/auth/me")).json()["data"]["routineLastRunAt"] is None
    data = _payload(await _call(admin, token, "report_routine_run"))
    assert data["routineLastRunAt"]
    me = (await admin.get("/api/auth/me")).json()["data"]
    assert me["routineLastRunAt"] == data["routineLastRunAt"]
    checks = {c["key"]: c["ok"] for c in (await admin.get("/api/auth/me/readiness")).json()["data"]["checks"]}
    assert checks["routine"] is True
