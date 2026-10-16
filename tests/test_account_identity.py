"""Persistent UUIDs, legacy import, backups and session-isolation regression."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
from Settings.device_modes import stored_profile_directory


def test_two_accounts_have_stable_uuid_paths_and_no_secret_in_public_view(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils
    root = tmp_path / "__profiles"
    monkeypatch.setattr(utils, "PROFILES_DIR", root)
    path = tmp_path / "Nexora" / "generated_profiles.json"
    store = LocalProfileStore(path)
    first = store.create("mae", device_mode="android", start_url="https://first.test")
    second = store.create("mae", device_mode="iphone", start_url="https://second.test")
    assert uuid.UUID(first["accountId"]).version == 4
    assert uuid.UUID(first["deviceProfileId"]).version == 4
    assert len({first["accountId"], second["accountId"]}) == 2
    assert len({first["deviceProfileId"], second["deviceProfileId"]}) == 2
    assert first["profilePath"] != second["profilePath"]
    assert stored_profile_directory(root, first) == Path(first["profilePath"])
    reopened = LocalProfileStore(path).get(first["id"])
    assert reopened["accountId"] == first["accountId"]
    assert reopened["deviceProfileId"] == first["deviceProfileId"]
    assert reopened["profilePath"] == first["profilePath"]
    visible = store.public_list()
    assert all("password" not in account and "credentialRef" not in account for account in visible)
    detail = store.public_detail(first["id"])
    assert detail["accountId"] == first["accountId"]
    assert detail["profilePath"] == first["profilePath"]
    assert "password" not in detail


def test_old_generated_profile_keeps_id_password_and_history_on_upgrade(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils
    monkeypatch.setattr(utils, "PROFILES_DIR", tmp_path / "__profiles")
    path = tmp_path / "Nexora" / "generated_profiles.json"
    path.parent.mkdir(parents=True)
    old = {
        "nextNumber": 38,
        "profiles": [{
            "id": 36, "label": "Conta 36", "origin": "mae",
            "deviceMode": "android", "username": "existing",
            "password": "preexisting", "confirmPassword": "preexisting",
            "createdAt": "2024-01-01T00:00:00Z", "status": "pending",
        }],
    }
    path.write_text(json.dumps(old), encoding="utf-8")
    store = LocalProfileStore(path)
    item = store.get(36)
    assert item["id"] == 36
    assert item["password"] == "preexisting"
    assert item["accountId"]
    assert item["profilePath"].endswith("account_36")
    assert store.create("mae")["id"] == 38
    backups = list((path.parent / "profile-backups").glob("*.json"))
    assert backups
    assert json.loads(backups[0].read_text(encoding="utf-8")) == old
    same = LocalProfileStore(path).get(36)
    assert same["accountId"] == item["accountId"]
    assert same["deviceProfileId"] == item["deviceProfileId"]


def test_main_branch_accounts_json_import_keeps_original_uuid_and_old_folder(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils
    from Settings.device_modes import profile_directory
    base = tmp_path / "historic_profiles"
    monkeypatch.setattr(utils, "PROFILES_DIR", base)
    path = tmp_path / "Nexora" / "generated_profiles.json"
    path.parent.mkdir(parents=True)
    account_id = str(uuid.uuid4())
    original = {
        "version": 1, "sites": [], "accounts": [{
            "id": account_id, "profileScope": "mae",
            "profileMode": "android", "profileIndex": 7,
            "displayName": "Conta histórica", "loginIdentifier": "olduser",
            "status": "local", "createdAt": "2025-01-01T00:00:00Z",
        }], "batches": [], "jobs": [], "audit": [],
    }
    main = path.with_name("accounts.json")
    main.write_text(json.dumps(original), encoding="utf-8")
    original_dir = profile_directory(base, "android", 7)
    original_dir.mkdir(parents=True)
    (original_dir / "cookies.sqlite").write_text("legacy cookies")
    store = LocalProfileStore(path)
    account = store.list()[0]
    assert account["accountId"] == account_id
    assert account["profilePath"] == str(original_dir.resolve())
    assert account["legacySource"] == "accounts.json"
    assert "runtimeConfig" not in account
    assert json.loads(main.read_text(encoding="utf-8")) == original
    assert (original_dir / "cookies.sqlite").read_text() == "legacy cookies"
    assert len(LocalProfileStore(path).list()) == 1


def test_unsafe_stored_path_is_rejected(tmp_path):
    profile = {"id": 1, "origin": "mae", "profilePath": str(tmp_path.parent / "other")}
    with pytest.raises(ValueError):
        stored_profile_directory(tmp_path / "profiles", profile)


def test_bad_source_json_is_not_replaced_with_empty_profiles(tmp_path):
    path = tmp_path / "generated_profiles.json"
    path.write_text("{BROKEN", encoding="utf-8")
    with pytest.raises(LocalProfileError):
        LocalProfileStore(path)
    assert path.read_text(encoding="utf-8") == "{BROKEN"
