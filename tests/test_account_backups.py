"""Local-only full account/cookie snapshots and safe batch deletion."""
from __future__ import annotations
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import servidor
import nexora_accounts.local_profiles as local_profiles
import nexora_accounts.proxy_store as proxy_store
from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
from nexora_accounts.profile_cleanup import delete_account
from nexora_accounts.account_backups import list_snapshots, restore_snapshot


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(local_profiles, "_default_path", lambda: tmp_path / "Nexora" / "generated_profiles.json")
    monkeypatch.setattr(proxy_store, "_default_path", lambda: tmp_path / "Nexora" / "proxies.json")
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae-profiles")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha-profiles")
    return LocalProfileStore()


def test_backup_contains_browser_cookies_and_preserves_all_account_ids(storage):
    first = storage.create("mae", device_mode="android", connection_mode="direct")
    second = storage.create("mae", device_mode="iphone", connection_mode="direct")
    browser = Path(first["profilePath"])
    (browser / "Default" / "Network").mkdir(parents=True)
    (browser / "Default" / "Network" / "Cookies").write_bytes(b"encrypted-test-cookie-db")
    (browser / "Default" / "Local Storage").mkdir()
    (browser / "Default" / "Local Storage" / "data").write_bytes(b"account-1-value")
    second_dir = Path(second["profilePath"])
    second_dir.mkdir(parents=True)
    (second_dir / "other").write_text("untouched")
    expected_uuid = first["accountId"]
    expected_device = first["deviceProfileId"]
    expected_ua = first.get("runtimeConfig")
    result = delete_account(first["id"])
    assert result["backupId"]
    assert not browser.exists()
    assert (second_dir / "other").read_text() == "untouched"
    backup = next(x for x in list_snapshots(store=storage) if x["id"] == result["backupId"])
    assert backup["fileCount"] == 2
    assert not backup["restored"]
    assert backup["hasBrowserData"]
    restored = restore_snapshot(result["backupId"], store=storage)
    profile = storage.get(restored["profileId"])
    assert profile["accountId"] == expected_uuid
    assert profile["deviceProfileId"] == expected_device
    assert profile.get("runtimeConfig") == expected_ua
    assert (browser / "Default" / "Network" / "Cookies").read_bytes() == b"encrypted-test-cookie-db"
    assert (browser / "Default" / "Local Storage" / "data").read_bytes() == b"account-1-value"
    assert list_snapshots(store=storage)[0]["restored"]


def test_large_browser_data_is_streamed_and_restored(storage):
    profile = storage.create("mae", device_mode="android")
    root = Path(profile["profilePath"])
    cache = root / "Default" / "Cache"
    cache.mkdir(parents=True)
    original_bytes = (b"chromium-data-" * 100000)
    (cache / "blob").write_bytes(original_bytes)
    info = delete_account(profile["id"])
    assert info["backupId"]
    restore_snapshot(info["backupId"], store=storage)
    assert (cache / "blob").read_bytes() == original_bytes


def test_original_proxy_assignment_is_preserved_on_restore(storage):
    from nexora_accounts.proxy_store import ProxyStore
    account = storage.create("mae", connection_mode="managed")
    proxy = ProxyStore().save_line("http://demo:pass@proxy.test:8123")
    ProxyStore().bind_profile(account["id"], proxy["id"])
    # Simulate a profile created before proxyId was also written into
    # the account record; the separate persistent binding is authoritative.
    assert not storage.get(account["id"]).get("proxyId")
    result = delete_account(account["id"])
    assert str(account["id"]) not in ProxyStore().profile_bindings()
    restore_snapshot(result["backupId"], store=storage)
    assert ProxyStore().profile_bindings()[str(account["id"])] == proxy["id"]
    assert storage.get(account["id"])["proxyId"] == proxy["id"]


def test_unreadable_backup_blocks_deletion_and_preserves_profile(storage, monkeypatch):
    profile = storage.create("filha", device_mode="android")
    path = Path(profile["profilePath"])
    path.mkdir(parents=True)
    (path / "cookie").write_text("keep")
    from nexora_accounts import account_backups
    monkeypatch.setattr(account_backups, "_seal",
                        lambda data: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(LocalProfileError, match="Falha ao preparar o backup"):
        delete_account(profile["id"])
    assert storage.get(profile["id"])["accountId"] == profile["accountId"]
    assert (path / "cookie").read_text() == "keep"
    assert list_snapshots(store=storage) == []


def test_restore_rejects_existing_identity_and_damaged_cookie(storage):
    profile = storage.create("mae")
    folder = Path(profile["profilePath"])
    folder.mkdir(parents=True)
    (folder / "Cookies").write_bytes(b"cookie")
    snapshot_id = delete_account(profile["id"])["backupId"]
    backup_dir = storage.path.parent / "deleted-account-backups" / snapshot_id
    (backup_dir / "browser" / "Cookies.dat").write_bytes(b"tampered")
    with pytest.raises(LocalProfileError):
        restore_snapshot(snapshot_id, store=storage)
    assert not storage.list()
    assert not folder.exists()


def test_single_and_bulk_delete_endpoint_require_confirm_and_create_backups(storage):
    a = storage.create("mae")
    b = storage.create("filha")
    with TestClient(servidor.app) as client:
        token = client.get("/api/state").json()["csrf_token"]
        headers = {"X-LixoCPA-CSRF": token}
        assert client.post("/api/local-profiles/delete-batch", json={
            "ids": [a["id"], b["id"]]
        }, headers=headers).status_code == 422
        response = client.post("/api/local-profiles/delete-batch", json={
            "ids": [a["id"], b["id"]], "confirmed": True,
        }, headers=headers)
        assert response.status_code == 200
        result = response.json()
        assert len(result["deleted"]) == 2
        assert result["errors"] == []
        assert not storage.list()
        backups = client.get("/api/account-backups").json()["backups"]
        assert len(backups) == 2
        assert all(entry["id"] and entry["accountId"] and "password" not in entry for entry in backups)
        recovered = client.post("/api/account-backups/restore", json={
            "backupId": result["deleted"][0]["backupId"], "confirmed": True
        }, headers=headers)
        assert recovered.status_code == 200
        assert recovered.json()["profileId"] == a["id"]
        assert storage.get(a["id"])["accountId"] == a["accountId"]
