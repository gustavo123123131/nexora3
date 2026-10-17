"""Regression: Mãe, Filha and Contas share account-owned Chromium diagnostics.

No real browser/network/Pix API request occurs; the Playwright Page is stubbed.
"""
import importlib
import threading
import time
from uuid import uuid4

import pytest

from nexora_accounts.browser_sessions import (
    active_account_session, claim_account, mark_browser_ready,
    AccountSessionBusy,
)
from nexora_accounts.local_profiles import LocalProfileStore
import nexora_accounts.session_launcher as launcher_module


def test_broker_scopes_by_origin_id_and_immutable_account_uuid():
    a, b = str(uuid4()), str(uuid4())
    stop = threading.Event()
    with claim_account("mae", 91812, account_uuid=a, stop_event=stop) as session:
        assert active_account_session("mae", 91812, a) is session
        assert active_account_session("filha", 91812, a) is None
        assert active_account_session("mae", 91812, b) is None
        assert not session["ready"]
        mark_browser_ready(session)
        assert session["ready"]
        with pytest.raises(AccountSessionBusy):
            with claim_account("mae", 91812, account_uuid=a, stop_event=stop):
                pass
    assert active_account_session("mae", 91812, a) is None


@pytest.mark.parametrize("origin,module_name", [
    ("mae", "Settings.Criar_Mae.gui_tab"),
    ("filha", "Settings.Criar_Filha.gui_tab"),
])
def test_new_account_browser_from_either_tab_is_usable_by_operations(
        origin, module_name, monkeypatch):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "discover_extensions", lambda _: [])

    profile_id = 93001 if origin == "mae" else 93002
    account_id = str(uuid4())
    profile = {
        "id": profile_id, "accountId": account_id, "origin": origin,
        "label": "Conta de teste", "platformUserId": None,
        "platformObservedUserId": "70022155",
    }
    monkeypatch.setattr(LocalProfileStore, "record_session", lambda *a, **kw: None)

    class ProfileSource:
        def public_detail(self, identifier):
            assert identifier == profile_id
            return dict(profile)

    monkeypatch.setattr(launcher_module, "LocalProfileStore", ProfileSource)

    created = threading.Event()
    stop = threading.Event()
    captured = {}
    page_thread_ids = []
    class StubPage:
        url = "https://www.5gbet.com/"

        def evaluate(self, script):
            assert "security/status" in script
            page_thread_ids.append(threading.get_ident())
            return {"state": "api-reachable"}

    fake_page = StubPage()

    def fake_run_instance(*args):
        plan = args[20]
        assert plan["account_profile"]["accountId"] == account_id
        plan["on_browser_ready"]()
        captured["owner_thread"] = threading.get_ident()
        created.set()
        while not stop.is_set():
            plan["on_browser_command_tick"](fake_page)
            time.sleep(0.01)

    monkeypatch.setattr(module, "run_instance", fake_run_instance)
    plan = {
        "idx": 1, "url": "https://www.5gbet.com",
        "account_profile": profile, "run_id": str(uuid4()),
    }
    def start():
        module.run_all(
            urls_string="https://www.5gbet.com",
            mode="android", count_per_url=1, headless=True,
            proxy=None, auto_goto=True, keep_profiles=True,
            run_auto=False, start_game_enabled=False,
            start_game_name="", start_game_id="",
            stop_event=stop, winpos_controller=None, speed_controller=None,
            verify_phone_controller=None, mirror=None,
            withdraw_controller=None, game_launch_controller=None,
            instance_target_controller=None,
            log=lambda msg: None, delay_seconds=0,
            instance_plans=[plan],
        )

    worker = threading.Thread(target=start)
    worker.start()
    try:
        assert created.wait(3), "Worker did not register account"
        live = active_account_session(origin, profile_id, account_id)
        assert live is not None and live["ready"]
        assert active_account_session(origin, profile_id, str(uuid4())) is None
        result = launcher_module.AccountLauncher().check_pix_browser_session(
            profile_id, account_id, timeout=3,
        )
        assert result["state"] == "api-reachable"
        assert result["identityStatus"] == "observed"
        assert len(page_thread_ids) == 1
        assert page_thread_ids[0] == captured["owner_thread"]
    finally:
        stop.set()
        worker.join(3)
    assert not worker.is_alive()
    assert active_account_session(origin, profile_id, account_id) is None
