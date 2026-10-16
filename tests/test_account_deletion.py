"""Delete just one saved account with confirmation and browser-use locking."""
import pytest
from fastapi.testclient import TestClient

import servidor
import nexora_accounts.local_profiles as local_profiles
import nexora_accounts.proxy_store as proxy_store
from nexora_accounts.browser_sessions import AccountSessionBusy, claim_account
from nexora_accounts.profile_cleanup import delete_account
from nexora_accounts.local_profiles import LocalProfileStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(local_profiles, "_default_path",
                        lambda: tmp_path / "Nexora" / "generated_profiles.json")
    monkeypatch.setattr(proxy_store, "_default_path",
                        lambda: tmp_path / "Nexora" / "proxies.json")
    from Settings.Criar_Mae.settings import utils as mae_utils
    from Settings.Criar_Filha.settings import utils as filha_utils
    monkeypatch.setattr(mae_utils, "PROFILES_DIR", tmp_path / "mae-profiles")
    monkeypatch.setattr(filha_utils, "PROFILES_DIR", tmp_path / "filha-profiles")
    return LocalProfileStore()


def test_delete_removes_only_requested_account_folder(store, tmp_path):
    a = store.create("mae")
    b = store.create("mae")
    from Settings.device_modes import account_profile_directory
    base = tmp_path / "mae-profiles"
    p_a = account_profile_directory(base, "mae", a["id"])
    p_b = account_profile_directory(base, "mae", b["id"])
    for path in (p_a, p_b):
        path.mkdir(parents=True)
        (path / "cookies.dat").write_text("cookie")
    shared = base / "profile_1"
    shared.mkdir(parents=True)
    (shared / "unknown.dat").write_text("legacy")
    store.select(a["id"])
    proxy = proxy_store.ProxyStore().save_line("proxy.test:8000:u:p")
    proxy_store.ProxyStore().bind_profile(a["id"], proxy["id"])

    result = delete_account(a["id"])
    assert result == {"ok": True, "profileId": a["id"]}
    assert not p_a.exists()
    assert (p_b / "cookies.dat").exists()
    assert (shared / "unknown.dat").exists()
    assert [p["id"] for p in store.list()] == [b["id"]]
    assert store.selected_id() is None
    assert str(a["id"]) not in proxy_store.ProxyStore().profile_bindings()
    assert proxy_store.ProxyStore().list()["proxies"][0]["id"] == proxy["id"]
    c = store.create("mae")
    assert c["id"] > b["id"]


def test_cannot_delete_account_open_in_browser(store):
    a = store.create("filha")
    with claim_account("filha", a["id"]):
        with pytest.raises(AccountSessionBusy):
            delete_account(a["id"])
    assert store.get(a["id"])["id"] == a["id"]


def test_delete_api_needs_confirm_and_csrf(store):
    a = store.create("mae")
    with TestClient(servidor.app) as client:
        csrf = client.get("/api/state").json()["csrf_token"]
        url = "/api/local-profiles/delete"
        data = {"id": a["id"], "confirmed": True}
        assert client.post(url, json=data).status_code == 403
        assert client.post(url, json={"id": a["id"]},
                           headers={"X-LixoCPA-CSRF": csrf}).status_code == 422
        good = client.post(url, json=data, headers={"X-LixoCPA-CSRF": csrf})
        assert good.status_code == 200
        assert good.json()["profileId"] == a["id"]
        assert not store.list()
