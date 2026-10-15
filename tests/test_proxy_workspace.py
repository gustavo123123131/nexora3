"""ProxyBet-style local proxy CRUD and browser routing regression tests.

All probes use in-memory sockets; no external proxy or site is accessed.
"""
import json
import stat

import pytest
from fastapi.testclient import TestClient

import servidor
from nexora_accounts.proxy_store import ProxyStore, ProxyError, active_proxy_config
import nexora_accounts.proxy_store as proxy_module
from Settings import local_proxy_gateway
from Settings.Criar_Mae.settings import browser as browser_config


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "Nexora" / "proxies.json"
    monkeypatch.setattr(proxy_module, "_default_path", lambda: path)
    return ProxyStore(path)


def test_save_edit_list_activate_delete_and_no_secret_leak(isolated_store):
    store = isolated_store
    created = store.save({
        "name": "Principal", "host": "proxy.example.net", "port": 8080,
        "protocol": "http", "username": "eu", "password": "senha-super-secreta",
    })
    assert created["name"] == "Principal"
    assert created["hasPassword"] is True
    assert "password" not in created
    assert "senha-super-secreta" not in json.dumps(store.list())
    assert store.list()["activeId"] is None
    assert store.active() is None
    assert store.activate(created["id"])["activeId"] == created["id"]
    assert active_proxy_config().server == "http://proxy.example.net:8080"
    assert active_proxy_config().username == "eu"
    assert active_proxy_config().password == "senha-super-secreta"

    changed = store.save({
        "id": created["id"], "name": "Novo nome",
        "host": "proxy2.example.net", "port": "9090",
        "protocol": "http", "username": "eu", "password": "",
    })
    assert changed["name"] == "Novo nome"
    assert store.active()["password"] == "senha-super-secreta"
    assert store.delete(created["id"]) == {"ok": True}
    assert store.list() == {"activeId": None, "proxies": []}
    assert store.active() is None
    assert isolated_store.path.exists()
    # chmod is meaningful under Linux; Windows may have different ACL handling.
    if hasattr(stat, "S_IMODE"):
        assert stat.S_IMODE(isolated_store.path.stat().st_mode) & 0o077 == 0


def test_validation_does_not_leak_password_in_errors(isolated_store):
    for payload in [
        {"name": "A", "host": "invalid host", "port": 8080, "protocol": "http"},
        {"name": "A", "host": "proxy.test", "port": 80000, "protocol": "http"},
        {"name": "A", "host": "proxy.test", "port": 8080, "protocol": "file"},
        {"name": "A", "host": "proxy.test", "port": 8080, "protocol": "http",
         "username": "bad:name", "password": "secret"},
    ]:
        with pytest.raises(ProxyError) as exc:
            isolated_store.save(payload)
        assert "secret" not in str(exc.value)


def test_gateway_uses_selected_http_proxy_per_instance(isolated_store, monkeypatch):
    item = isolated_store.save({
        "name": "A", "host": "127.0.0.1", "port": 9001,
        "protocol": "http", "username": "username",
        "password": "local-password",
    })
    isolated_store.activate(item["id"])
    monkeypatch.setattr(
        browser_config, "_apply_smartproxy_fixed_ip",
        lambda username, idx=None: username,
    )
    upstream = local_proxy_gateway._resolve_upstream_for_idx(7)
    assert upstream == {
        "server": "http://127.0.0.1:9001",
        "username": "username",
        "password": "local-password",
    }
    assert local_proxy_gateway._gateway_proxy_enabled() is True
    # Explicit Desativar must not fall back to a stale hidden legacy proxy.
    monkeypatch.setattr(local_proxy_gateway, "_raw_upstream_proxy",
                        lambda: "http://legacy.example.net:8080")
    isolated_store.activate(None)
    assert isolated_store.is_managed() is True
    assert active_proxy_config() is None
    assert local_proxy_gateway._gateway_proxy_enabled() is False
    assert local_proxy_gateway._resolve_upstream_for_idx(7) is None


def test_tcp_probe_is_explicitly_not_authentication(isolated_store):
    item = isolated_store.save({
        "name": "A", "host": "proxy.example.net", "port": 8080,
        "protocol": "http", "username": "owner", "password": "secret",
    })

    class FakeSocket:
        closed = False

        def close(self):
            self.closed = True

    fake = FakeSocket()
    seen = []
    result = isolated_store.test(item["id"], connect=lambda addr, timeout: (
        seen.append((addr, timeout)), fake
    )[1])
    assert result["reachable"] is True
    assert "Autenticação" in result["message"]
    assert seen == [(("proxy.example.net", 8080), 4)]
    assert fake.closed is True

    def unreachable(*args, **kwargs):
        raise TimeoutError("proxy.secret")
    failed = isolated_store.test(item["id"], connect=unreachable)
    assert failed["reachable"] is False
    assert "proxy.secret" not in str(failed)


def test_proxy_api_is_csrf_protected_and_never_exposes_password(isolated_store):
    with TestClient(servidor.app) as client:
        assert client.get("/api/proxies").json() == {"activeId": None, "proxies": []}
        data = {
            "name": "Servidor A", "host": "proxy.example.net", "port": 8080,
            "protocol": "http", "username": "usuario", "password": "SECRETO!",
        }
        assert client.post("/api/proxies/save", json=data).status_code == 403
        token = client.get("/api/state").json()["csrf_token"]
        headers = {"X-LixoCPA-CSRF": token}
        created = client.post("/api/proxies/save", json=data, headers=headers)
        assert created.status_code == 200, created.text
        record = created.json()["proxy"]
        assert record["hasPassword"] is True
        assert "SECRETO!" not in created.text
        listing = client.get("/api/proxies").json()
        assert listing["proxies"][0]["id"] == record["id"]
        assert "SECRETO!" not in json.dumps(listing)
        response = client.post(
            "/api/proxies/activate", headers=headers,
            json={"id": record["id"]}
        )
        assert response.status_code == 200
        assert client.get("/api/proxies").json()["activeId"] == record["id"]
        wrong = client.post("/api/proxies/delete", json={"id": record["id"]})
        assert wrong.status_code == 403
        removed = client.post(
            "/api/proxies/delete", json={"id": record["id"]}, headers=headers
        )
        assert removed.json()["ok"] is True
        assert client.get("/api/proxies").json()["activeId"] is None


def test_origin_validation_unchanged_for_proxy_api(isolated_store):
    with TestClient(servidor.app) as client:
        assert client.get(
            "/api/proxies", headers={"Host": "attacker.example"}
        ).status_code == 403
        assert client.post(
            "/api/proxies/save", json={}, headers={"Origin": "https://attacker.example"}
        ).status_code == 403
