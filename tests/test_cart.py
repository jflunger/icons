import json
import zipfile

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


def test_cart_zip_names_files_after_purpose(local):
    import io
    import zipfile
    assert local.get("/api/cart/zip", headers=PW).status_code == 404               # empty cart
    local.post("/api/local/shopping-list", json={"purposes": ["no icon yet"]})
    for purpose in ("Delete: row/item", "delete- row-item", ""):
        local.post("/api/cart/items", json={"icon_id": "fake:trash", "purpose": purpose}, headers=PW)
    assert local.get("/api/cart/zip").status_code == 401
    r = local.get("/api/cart/zip", headers=PW)
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()                       # no LICENSES/
    assert names == ["Delete- row-item.svg", "delete- row-item (2).svg", "trash.svg"]


# Host: the SDK rejects other hosts (DNS rebinding protection).
MCP_HEADERS = {"Host": "127.0.0.1:9005", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}


def mcp_call(c, method, params=None, headers=None):
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                  headers={**MCP_HEADERS, **(headers or {})})


def mcp_tool(c, name, **arguments):
    """(is_error, result): the tool's dict, or its error message."""
    res = mcp_call(c, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
    text = res["content"][0]["text"]
    return bool(res.get("isError")), text if res.get("isError") else json.loads(text)


def test_mcp_rejects_proxied_and_remote(client, local):
    assert mcp_call(client, "tools/list").status_code == 403
    assert mcp_call(local, "tools/list", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


def test_mcp_tools(local, tmp_path):
    r = mcp_call(local, "tools/list")
    assert r.status_code == 200, r.text
    assert sorted(t["name"] for t in r.json()["result"]["tools"]) == ["add_to_shopping_list", "get_cart_zip"]

    assert mcp_tool(local, "add_to_shopping_list", purposes=["Delete button", "Back"]) == (False, {
        "added": ["Delete button", "Back"], "already_in_cart": [], "waiting_for_icon": ["Delete button", "Back"]})
    assert mcp_tool(local, "add_to_shopping_list", purposes=["Back"])[1]["already_in_cart"] == ["Back"]

    error, msg = mcp_tool(local, "get_cart_zip", path=str(tmp_path))
    assert error and "no icons yet" in msg
    error, msg = mcp_tool(local, "get_cart_zip", path="relative.zip")
    assert error and msg.endswith("path must be absolute")

    entry = next(i for i in local.get("/api/cart", headers=PW).json()["items"] if i["purpose"] == "Delete button")
    local.patch(f"/api/cart/items/{entry['id']}", json={"icon_id": "fake:trash"}, headers=PW)
    error, out = mcp_tool(local, "get_cart_zip", path=str(tmp_path))
    assert not error and out == {"path": str(tmp_path / "icon-cart.zip"), "files": ["Delete button.svg"], "without_icon": ["Back"]}
    with zipfile.ZipFile(out["path"]) as zf:
        assert zf.namelist() == ["Delete button.svg"]
