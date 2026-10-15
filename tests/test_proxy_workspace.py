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


def test_one_line_import_format_persistence_and_masking(isolated_store):
    store = isolated_store
    a = store.save_line("proxy-one.example.com:8080:cliente:senha123")
    b = store.save_line("https://ana:abc%40xyz@proxy-two.example.com:443")
    c = store.save_line("socks5://u:p@proxy-three.example.com:1080")
    d = store.save_line("proxy-four.example.com:8899")
    assert [p["protocol"] for p in store.list()["proxies"]] == [
        "http", "https", "socks5", "http",
    ]
    assert store.is_managed()
    assert "senha123" not in json.dumps(store.list())
    assert "abc@xyz" not in json.dumps(store.list())
    assert ProxyStore(store.path).list()["proxies"] == store.list()["proxies"]
    assert store.assign(1)[0]["password"] == "senha123"
    assert store.assign(1)[0]["password"] == "abc@xyz"
    assert a["id"] != b["id"] != c["id"] != d["id"]


def test_proxies_assigned_per_session_without_changing_provider_credentials(isolated_store):
    store = isolated_store
    a = store.save_line("http://userA:passA@first.example.com:7001")
    b = store.save_line("socks5://userB:passB@second.example.com:7002")
    pairs = store.assign(2)
    assert [x["id"] for x in pairs] == [a["id"], b["id"]]
    assert [x["username"] for x in pairs] == ["userA", "userB"]
    assert [x["password"] for x in pairs] == ["passA", "passB"]
    assert store.assign(1)[0]["id"] == a["id"]
    # Another tab or a restarted process continues from the saved cursor.
    assert ProxyStore(store.path).assign(1)[0]["id"] == b["id"]
    assert [x["id"] for x in store.assign(3)] == [a["id"], b["id"], a["id"]]


@pytest.mark.parametrize("raw", [
    "", "invalid", "ftp://proxy.example.com:8080", "proxy.example.com:99999",
    "socks5://proxy.example.com:0", "proxy.example.com:80:bad:pass\u0001word",
])
def test_invalid_one_line_proxy_fails_without_saving(isolated_store, raw):
    with pytest.raises(ProxyError):
        isolated_store.save_line(raw)
    assert isolated_store.list()["proxies"] == []


def test_add_line_api_preserves_password_privacy(isolated_store):
    with TestClient(servidor.app) as client:
        csrf = client.get("/api/state").json()["csrf_token"]
        headers = {"X-LixoCPA-CSRF": csrf}
        assert client.post("/api/proxies/add-line",
                           json={"line": "proxy.example.com:8888:u:secret"}).status_code == 403
        resp = client.post("/api/proxies/add-line",
                           json={"line": "proxy.example.com:8888:u:secret"}, headers=headers)
        assert resp.status_code == 200
        assert "secret" not in resp.text
        listing = client.get("/api/proxies")
        assert "secret" not in listing.text
        assert listing.json()["proxies"][0]["protocol"] == "http"


def test_account_proxy_binding_survives_restart(isolated_store, monkeypatch, tmp_path):
    import nexora_accounts.local_profiles as profile_module
    from nexora_accounts.local_profiles import LocalProfileStore
    monkeypatch.setattr(profile_module, "_default_path",
                        lambda: tmp_path / "Nexora" / "generated_profiles.json")
    profile = LocalProfileStore().create("mae")
    a = isolated_store.save_line("http://alice:secret@one.example.com:8001")
    b = isolated_store.save_line("http://bob:secret@two.example.com:8002")
    assert isolated_store.bind_profile(profile["id"], b["id"])["proxyId"] == b["id"]
    reopened = ProxyStore(isolated_store.path)
    assert reopened.profile_bindings()[str(profile["id"])] == b["id"]
    allocated = reopened.assign(2, profile_id=profile["id"])
    assert [x["id"] for x in allocated] == [b["id"], a["id"]]
    assert reopened.delete(b["id"]) == {"ok": True}
    assert str(profile["id"]) not in reopened.profile_bindings()


def test_profile_bindings_api_persists_and_checks_csrf(isolated_store, monkeypatch, tmp_path):
    import nexora_accounts.local_profiles as profile_module
    from nexora_accounts.local_profiles import LocalProfileStore
    monkeypatch.setattr(profile_module, "_default_path",
                        lambda: tmp_path / "Nexora" / "generated_profiles.json")
    profile = LocalProfileStore().create("filha")
    proxy = isolated_store.save_line("example.proxy.test:9000:demo:secret")
    with TestClient(servidor.app) as client:
        csrf = client.get("/api/state").json()["csrf_token"]
        data = {"profileId": profile["id"], "proxyId": proxy["id"]}
        assert client.post("/api/proxies/bind-profile", json=data).status_code == 403
        assert client.post("/api/proxies/bind-profile", json=data,
                           headers={"X-LixoCPA-CSRF": csrf}).status_code == 200
        result = client.get("/api/proxies/profile-bindings").json()["bindings"]
        assert result[str(profile["id"])] == proxy["id"]
        assert "secret" not in json.dumps(result)
