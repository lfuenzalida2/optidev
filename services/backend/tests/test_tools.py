async def test_md5_requires_auth(client):
    assert (await client.post("/api/tools/md5", json={"text": "hola"})).status_code == 401


async def test_md5(admin):
    resp = await admin.post("/api/tools/md5", json={"text": "hola"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["md5"] == "4d186321c1a7f0f354b297e8914ab240"
    # String vacío y no-ASCII (se hashea en UTF-8).
    assert (await admin.post("/api/tools/md5", json={"text": ""})).json()["data"]["md5"] == "d41d8cd98f00b204e9800998ecf8427e"
    assert (await admin.post("/api/tools/md5", json={"text": "ñ"})).json()["data"]["md5"] == "94e9342ecbb1458b6043ecd3bfcbc192"
