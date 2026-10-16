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
    assert result["ok"] is True
    assert result["profileId"] == a["id"]
    assert result["filesRemoved"] is True
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


def test_delete_card_only_preserves_its_browser_files(store, tmp_path):
    from Settings.device_modes import account_profile_directory
    p = store.create("mae", device_mode="android")
    folder = account_profile_directory(tmp_path / "mae-profiles", "mae", p["id"])
    folder.mkdir(parents=True)
    (folder / "Local Storage").mkdir()
    (folder / "Local Storage" / "db").write_text("keep me")
    response = delete_account(p["id"], remove_files=False)
    assert response["filesRemoved"] is False
    assert (folder / "Local Storage" / "db").read_text() == "keep me"
    assert store.list() == []
    backups = list((store.path.parent / "profile-backups").glob("*.json"))
    assert backups, "Metadata backup must exist before account removal"


@pytest.mark.parametrize("batch", [False, True])
def test_deletion_backup_does_not_block_agent_health(store, monkeypatch, batch):
    """A slow Chromium backup cannot freeze unrelated requests to the agent."""
    import asyncio
    import threading
    import time
    import httpx
    from nexora_accounts import profile_cleanup

    item = store.create("mae", device_mode="android")
    started = threading.Event()
    release = threading.Event()

    def slow_deletion(profile_id, *, remove_files=True):
        assert profile_id == item["id"]
        started.set()
        release.wait(timeout=2.0)
        return {"ok": True, "profileId": profile_id,
                "filesRemoved": remove_files, "backupId": "test-snapshot"}

    monkeypatch.setattr(profile_cleanup, "delete_account", slow_deletion)

    async def check():
        transport = httpx.ASGITransport(app=servidor.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            headers = {"X-LixoCPA-CSRF": servidor._CSRF_TOKEN}
            url = "/api/local-profiles/delete-batch" if batch else "/api/local-profiles/delete"
            data = ({"ids": [item["id"]], "confirmed": True} if batch else
                    {"id": item["id"], "confirmed": True, "removeFiles": True})
            start = time.monotonic()
            task = asyncio.create_task(client.post(url, json=data, headers=headers))
            try:
                assert await asyncio.to_thread(started.wait, 1.0)
                health = await asyncio.wait_for(client.get("/api/health"), timeout=0.75)
                assert health.status_code == 200
                # Without offloading the backup, the event loop freezes until
                # the slow operation's timeout expires (~2 seconds).
                assert time.monotonic() - start < 1.5
            finally:
                release.set()
            response = await asyncio.wait_for(task, timeout=2)
            assert response.status_code == 200
            if batch:
                assert response.json()["deleted"][0]["profileId"] == item["id"]
            else:
                assert response.json()["profileId"] == item["id"]

    asyncio.run(check())
