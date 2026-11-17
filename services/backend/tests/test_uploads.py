import base64

from tests.test_board import _list_id, _new_card
from tests.test_mcp import _call, _token

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg=="
)


async def _upload(client, data, name="x.png", content_type="image/png", **kwargs):
    return await client.post("/api/uploads", files={"file": (name, data, content_type)}, **kwargs)


async def test_upload_and_serve_image(admin):
    resp = await _upload(admin, PNG)
    assert resp.status_code == 201, resp.text
    image = resp.json()["data"]
    assert image["url"].startswith("/api/uploads/") and image["url"].endswith(".png")

    # Servir no requiere auth: lo pide un <img>, que no manda el header.
    served = await admin.get(image["url"], headers={"Authorization": ""})
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG


async def test_upload_validation(admin):
    assert (await _upload(admin, PNG, headers={"Authorization": ""})).status_code == 401
    # El tipo sale de los bytes, no del content-type declarado.
    assert (await _upload(admin, b"<svg onload=alert(1)>", "x.svg", "image/svg+xml")).status_code == 415
    assert (await _upload(admin, b"not an image", "x.png", "image/png")).status_code == 415
    assert (await _upload(admin, PNG + b"\0" * (5 * 1024 * 1024))).status_code == 413
    assert (await admin.get("/api/uploads/..%2F..%2Foptidev.db")).status_code == 404
    assert (await admin.get("/api/uploads/" + "0" * 32 + ".png")).status_code == 404


async def test_card_images_reach_mcp(admin):
    url = (await _upload(admin, PNG)).json()["data"]["url"]
    other = (await _upload(admin, PNG)).json()["data"]["url"]
    card = await _new_card(admin, await _list_id(admin, "Pendiente"), description=f"Mirá esto:\n\n![captura]({url})")
    await admin.post(f"/api/cards/{card['id']}/comments", json={"body": f"otra ![]({other}) y de nuevo ![]({url})"})

    detail = (await admin.get(f"/api/cards/{card['id']}")).json()["data"]
    assert detail["images"] == [url, other]

    token = await _token(admin)
    result = await _call(admin, token, "get_card_images", {"card_id": card["id"]})
    images = [c for c in result["content"] if c["type"] == "image"]
    assert len(images) == 2
    assert images[0]["mimeType"] == "image/png"
    assert base64.b64decode(images[0]["data"]) == PNG


async def test_edit_profile(admin):
    url = (await _upload(admin, PNG)).json()["data"]["url"]
    resp = await admin.patch("/api/auth/me", json={"name": " Admin Optidev ", "avatarUrl": url, "avatarColor": "#FA8C16"})
    assert resp.status_code == 200, resp.text
    me = resp.json()["data"]
    assert (me["name"], me["avatarUrl"], me["avatarColor"]) == ("Admin Optidev", url, "#fa8c16")

    # El avatar viaja con el usuario en las tarjetas.
    card = await _new_card(admin, await _list_id(admin, "Pendiente"))
    assert card["assignee"]["avatarUrl"] == url

    for bad in ({"avatarUrl": "https://evil.example/x.png"}, {"avatarUrl": "/api/uploads/" + "0" * 32 + ".png"},
                {"avatarColor": "red"}, {"name": "  "}):
        assert (await admin.patch("/api/auth/me", json=bad)).status_code in (400, 422), bad

    # null vuelve al default; lo que no se manda no cambia.
    me = (await admin.patch("/api/auth/me", json={"avatarUrl": None})).json()["data"]
    assert me["avatarUrl"] is None and me["avatarColor"] == "#fa8c16" and me["name"] == "Admin Optidev"
