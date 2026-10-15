"""Regression checks: clean independent sessions, exact saved account and device.

No external registrations, websites or proxy connections are performed.
"""
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from fastapi.testclient import TestClient

import nexora_accounts.local_profiles as profile_module
import nexora_accounts.proxy_store as proxy_module
from nexora_accounts.local_profiles import LocalProfileStore
from nexora_accounts.session_launcher import AccountLauncher
from Settings.device_modes import account_profile_directory, profile_mode
import servidor


@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_module, "_default_path",
                        lambda: tmp_path / "Nexora" / "generated_profiles.json")
    monkeypatch.setattr(proxy_module, "_default_path",
                        lambda: tmp_path / "Nexora" / "proxies.json")
    return LocalProfileStore()


def test_each_created_account_has_own_device_url_and_directory(isolated_profiles, tmp_path):
    store = isolated_profiles
    a = store.create("mae", device_mode="android", start_url="https://a.example")
    b = store.create("mae", device_mode="iphone", start_url="https://b.example")
    c = store.create("filha", device_mode="android", start_url="https://c.example")
    assert [x["id"] for x in (a, b, c)] == [1, 2, 3]
    assert store.get(2)["deviceMode"] == "iphone"
    assert store.get(2)["startUrl"] == "https://b.example"
    path_a = account_profile_directory(tmp_path, "mae", 1)
    path_b = account_profile_directory(tmp_path, "mae", 2)
    path_c = account_profile_directory(tmp_path, "filha", 3)
    assert len({path_a, path_b, path_c}) == 3
    assert "profile_1" not in str(path_a)
    assert path_a != path_b
    assert profile_mode("mixed", 1) == "android"
    assert profile_mode("mixed", 2) == "iphone"


def test_new_account_does_not_reuse_selected_account(isolated_profiles):
    store = isolated_profiles
    first = store.create("mae", device_mode="android")
    store.select(first["id"])
    second = store.create("mae", device_mode="iphone")
    assert first["id"] != second["id"]
    assert first["username"] != second["username"]
    assert store.selected()["id"] == first["id"]
    assert store.get(second["id"])["deviceMode"] == "iphone"


def test_legacy_card_requires_explicit_device_and_remembers_it(isolated_profiles):
    store = isolated_profiles
    old = store.create("mae")
    assert "deviceMode" not in old
    configured = store.configure_legacy_launch(
        old["id"], "iPhone", "exemplo.com",
    )
    assert configured["deviceMode"] == "iphone"
    assert configured["startUrl"] == "https://exemplo.com"
    assert LocalProfileStore().get(old["id"])["deviceMode"] == "iphone"
    # A second opening cannot silently change a saved device.
    changed = store.configure_legacy_launch(old["id"], "android")
    assert changed["deviceMode"] == "iphone"


def test_open_card_api_uses_exact_id_not_global_selection(isolated_profiles, monkeypatch):
    from Settings.Criar_Mae.settings.browser import ModeConfig
    from dataclasses import asdict
    from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
    a = isolated_profiles.create("mae", device_mode="android")
    b = isolated_profiles.create("filha", device_mode="iphone")
    isolated_profiles.select(a["id"])
    isolated_profiles.set_runtime_config(b["id"], asdict(ModeConfig(
        "iphone", "Mozilla/5.0 iPhone Safari", {"width": 390, "height": 844},
        3.0, True, True, "Apple", "Apple GPU", {},
    )))
    observed = []
    monkeypatch.setattr(
        ACCOUNT_LAUNCHER, "launch",
        lambda gui, profile_id: (observed.append(profile_id),
                                {"ok": True, "profileId": profile_id})[1],
    )
    with TestClient(servidor.app) as client:
        csrf = client.get("/api/state").json()["csrf_token"]
        response = client.post(
            "/api/local-profiles/open", json={"id": b["id"]},
            headers={"X-LixoCPA-CSRF": csrf},
        )
    assert response.status_code == 200
    assert response.json()["profileId"] == b["id"]
    assert observed == [b["id"]]
    assert isolated_profiles.selected()["id"] == a["id"]


def test_saved_card_launcher_reuses_only_its_own_data(isolated_profiles, monkeypatch):
    a = isolated_profiles.create(
        "mae", device_mode="android", start_url="https://a.example",
    )
    b = isolated_profiles.create(
        "mae", device_mode="iphone", start_url="https://b.example",
        connection_mode="direct",
    )
    isolated_profiles.select(a["id"])
    from Settings.Criar_Mae.settings.browser import ModeConfig
    from dataclasses import asdict
    isolated_profiles.set_runtime_config(b["id"], asdict(ModeConfig(
        "iphone", "Mozilla/5.0 iPhone Safari", {"width": 390, "height": 844},
        3.0, True, True, "Apple", "Apple GPU", {},
    )))

    seen = []
    finished = threading.Event()

    def fake_run_all(**kwargs):
        seen.append(kwargs)
        finished.set()

    class FakeTab:
        def __init__(self):
            self.headless_var = SimpleNamespace(get=lambda: False)
            self.proxy_var = SimpleNamespace(get=lambda: "")
            self.winpos_controller = object()
            self.speed_controller = object()
            self.verify_phone_controller = object()
            self.mirror = object()
            self.withdraw_controller = object()
            self.game_launch_controller = object()
            self.instance_target_controller = object()

        def _validate_proxy(self):
            return None

        def after(self, delay, callback):
            callback()

        def _log(self, message):
            pass

    tab = FakeTab()
    module = SimpleNamespace(run_all=fake_run_all)
    monkeypatch.setattr(
        AccountLauncher, "_controller",
        staticmethod(lambda gui, origin: (module, tab)),
    )
    launcher = AccountLauncher()
    result = launcher.launch(None, b["id"])
    assert result["profileId"] == b["id"]
    assert finished.wait(3)
    assert len(seen) == 1
    call = seen[0]
    assert call["mode"] == "iphone"
    assert call["auto_goto"] is True
    assert call["urls_string"] == "https://b.example"
    assert call["stop_event"] is not None
    assert len(call["instance_plans"]) == 1
    plan = call["instance_plans"][0]
    assert plan["account_profile"]["id"] == b["id"]
    assert plan["account_profile"]["username"] == b["username"]
    assert plan["device_mode"] == "iphone"
    assert plan["url"] == "https://b.example"
    assert "registration_profile" not in plan


def test_cannot_share_a_live_browser_profile_directory():
    from nexora_accounts.browser_sessions import claim_account, AccountSessionBusy
    with claim_account("mae", 2):
        with pytest.raises(AccountSessionBusy):
            with claim_account("mae", 2):
                pass
        # Different account IDs are independent.
        with claim_account("mae", 3):
            pass
        # Different origins are independent.
        with claim_account("filha", 2):
            pass
    # Closing releases the account for a later re-open.
    with claim_account("mae", 2):
        pass


def test_runtime_snapshot_is_immutable_and_survives_restart(isolated_profiles):
    from dataclasses import asdict
    from Settings.Criar_Mae.settings.browser import ModeConfig
    profile = isolated_profiles.create("mae", device_mode="android")
    original = asdict(ModeConfig(
        "android",
        "Mozilla/5.0 (Linux; Android 13; SM-A035M Build/TP1A.220624.014) AppleWebKit/537.36",
        {"width": 412, "height": 915}, 2.75, True, True,
        "Qualcomm", "Adreno", {},
    ))
    saved = isolated_profiles.set_runtime_config(profile["id"], original)
    assert "SM-A035M" in saved["deviceLabel"]
    assert saved["runtimeConfig"] == original
    assert LocalProfileStore().get(profile["id"])["runtimeConfig"] == original
    from nexora_accounts.local_profiles import LocalProfileError
    from copy import deepcopy
    wrong = deepcopy(original)
    wrong["user_agent"] = "some other device"
    with pytest.raises(LocalProfileError):
        isolated_profiles.set_runtime_config(profile["id"], wrong)
    assert isolated_profiles.get(profile["id"])["runtimeConfig"] == original


def test_status_is_pending_until_site_confirms(isolated_profiles):
    store = isolated_profiles
    p = store.create("filha", device_mode="android")
    assert p["status"] == "pending"
    store.update_status(p["id"], "error", "Falha ao enviar.")
    assert store.get(p["id"])["status"] == "error"
    # Only an explicit confirmed-success event may call 'created'.
    store.update_status(p["id"], "created", "Cadastro confirmado pelo site.")
    assert store.get(p["id"])["status"] == "created"
    # Later navigation errors do not incorrectly revoke a confirmed account.
    store.update_status(p["id"], "error", "Proxy indisponível.")
    assert store.get(p["id"])["status"] == "created"


def test_legacy_profile_without_original_snapshot_is_rejected(isolated_profiles):
    store = isolated_profiles
    old = store.create("mae", device_mode="android")
    from nexora_accounts.session_launcher import AccountLauncher
    with pytest.raises(ValueError, match="perfil técnico original"):
        AccountLauncher().launch(None, old["id"])


def test_direct_account_connection_does_not_change_if_proxy_pool_changes(isolated_profiles, monkeypatch):
    from types import SimpleNamespace
    from Settings.Criar_Mae.settings.browser import ModeConfig
    from dataclasses import asdict
    from nexora_accounts.session_launcher import AccountLauncher
    profile = isolated_profiles.create("mae", device_mode="android", connection_mode="direct")
    isolated_profiles.set_runtime_config(profile["id"], asdict(ModeConfig(
        "android", "Android Test User Agent", {"width": 412, "height": 915},
        2.75, True, True, "Qualcomm", "Adreno", {},
    )))
    # Add a proxy AFTER the account was first created.
    proxy_module.ProxyStore().save_line("http://u:pwd@a.proxy.test:8500")
    seen = []
    ready = threading.Event()
    class Tab:
        headless_var = SimpleNamespace(get=lambda: False)
        winpos_controller = object()
        speed_controller = object()
        verify_phone_controller = object()
        mirror = object()
        withdraw_controller = object()
        game_launch_controller = object()
        instance_target_controller = object()
        def after(self, delay, fn):
            fn()
        def _log(self, message):
            pass
    def launch(**kwargs):
        seen.append(kwargs)
        ready.set()
    monkeypatch.setattr(AccountLauncher, "_controller",
                        staticmethod(lambda gui, origin: (SimpleNamespace(run_all=launch), Tab())))
    AccountLauncher().launch(None, profile["id"])
    assert ready.wait(4)
    assert seen[0]["proxy"] is None
    assert "proxy_config" not in seen[0]["instance_plans"][0]


def test_account_with_managed_proxy_without_original_binding_is_blocked(isolated_profiles, monkeypatch):
    from Settings.Criar_Mae.settings.browser import ModeConfig
    from dataclasses import asdict
    from nexora_accounts.session_launcher import AccountLauncher
    p = isolated_profiles.create("mae", device_mode="android", connection_mode="managed")
    isolated_profiles.set_runtime_config(p["id"], asdict(ModeConfig(
        "android", "Android Test User Agent", {"width": 412, "height": 915},
        2.75, True, True, "Qualcomm", "Adreno", {},
    )))
    proxy_module.ProxyStore().save_line("http://u:pwd@a.proxy.test:8500")
    messages = []
    class Tab:
        def after(self, delay, cb):
            cb()
        def _log(self, message):
            messages.append(message)
    from types import SimpleNamespace
    monkeypatch.setattr(AccountLauncher, "_controller",
                        staticmethod(lambda gui, origin: (SimpleNamespace(), Tab())))
    launcher = AccountLauncher()
    launcher.launch(None, p["id"])
    import time
    for _ in range(100):
        if messages:
            break
        time.sleep(0.01)
    assert any("vínculo da proxy original ausente" in msg for msg in messages)
