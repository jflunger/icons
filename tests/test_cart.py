import pytest
from fastapi.testclient import TestClient

PW = {"X-Cart-Password": "pw"}


@pytest.fixture
def local(client):
    """A client whose requests come from 127.0.0.1, like a script on the server."""
    from iconsearch import server
    with TestClient(server.app, client=("127.0.0.1", 50000)) as c:
        c.delete("/api/cart", headers=PW)
        yield c


def test_cart_needs_password(client):
    assert client.get("/api/cart").status_code == 401
    assert client.get("/api/cart", headers={"X-Cart-Password": "nope"}).status_code == 401
    assert client.get("/api/cart", headers=PW).status_code == 200


def test_cart_add_replace_edit_clear(local):
    items = local.post("/api/cart/items", json={"icon_id": "fake:trash"}, headers=PW).json()["items"]
    assert [(i["purpose"], i["icon"]["id"]) for i in items] == [("trash", "fake:trash")]
    entry = items[0]["id"]
    items = local.patch(f"/api/cart/items/{entry}", json={"icon_id": "fake:chevron"}, headers=PW).json()["items"]
    assert (items[0]["purpose"], items[0]["icon"]["id"]) == ("trash", "fake:chevron")
    items = local.patch(f"/api/cart/items/{entry}", json={"purpose": "Delete button"}, headers=PW).json()["items"]
    assert (items[0]["purpose"], items[0]["icon"]["id"]) == ("Delete button", "fake:chevron")
    assert local.post("/api/cart/items", json={"icon_id": "fake:nope"}, headers=PW).status_code == 404
    assert local.patch("/api/cart/items/missing", json={"purpose": "x"}, headers=PW).status_code == 404
    assert local.delete("/api/cart", headers=PW).json() == {"items": []}


def test_local_endpoints_reject_proxied_and_remote(client, local):
    assert client.get("/api/local/cart").status_code == 403          # remote client
    assert local.get("/api/local/cart", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert local.post("/api/local/shopping-list", json={"purposes": ["x"]},
                      headers={"X-Real-IP": "1.2.3.4"}).status_code == 403


def test_shopping_list_then_fill(local):
    r = local.post("/api/local/shopping-list", json={"purposes": ["Back arrow", "Delete", "Back arrow"]})
    items = r.json()["items"]
    assert [(i["purpose"], i["icon"]) for i in items] == [("Back arrow", None), ("Delete", None)]
    local.post("/api/local/shopping-list", json={"purposes": ["Delete"]})          # already there
    local.patch(f"/api/cart/items/{items[1]['id']}", json={"icon_id": "fake:trash"}, headers=PW)
    out = local.get("/api/local/cart", params={"color": "ff0000"}).json()["items"]
    assert [(i["purpose"], i["filename"]) for i in out] == [("Back arrow", None), ("Delete", "fake-trash.svg")]
    assert out[0]["svg"] is None and "#ff0000" in out[1]["svg"]
    assert local.post("/api/local/shopping-list", json={"purposes": "nope"}).status_code == 422
