"""Offline account-scoped Chromium API health-check regression tests.

Fake pages only; no credentials, Pix binding or live provider requests.
"""
from queue import Queue
import threading
from uuid import uuid4

import pytest

import nexora_accounts.session_launcher as launcher_module
from nexora_accounts.local_profiles import LocalProfileError
from nexora_accounts.session_launcher import AccountLauncher


class StubPage:
    url = "https://www.5gbet.com/"

    def __init__(self, response="api-reachable"):
        self.response = response
        self.evaluations = 0

    def evaluate(self, script):
        assert "security/status" in script
        assert "bindalipayV3" not in script
        assert "verifyWithdrawalPasswordV2" not in script
        self.evaluations += 1
        return {"state": self.response}


def test_browser_probe_accepts_only_supported_https_site():
    page = StubPage()
    page.url = "https://wrong.example/"
    result = AccountLauncher._probe_existing_page(page)
    assert result["state"] == "wrong-site"
    assert page.evaluations == 0
    page.url = "http://www.5gbet.com/"
    assert AccountLauncher._probe_existing_page(page)["state"] == "wrong-site"
    page.url = "https://www.5gbet.com/account"
    assert AccountLauncher._probe_existing_page(page)["state"] == "api-reachable"
    assert "UID ainda requer validação" in AccountLauncher._probe_existing_page(page)["message"]


def test_live_probe_runs_on_worker_and_never_crosses_account(monkeypatch):
    account_id = str(uuid4())
    bad_id = str(uuid4())
    profile = {"id": 12, "accountId": account_id, "platformUserId": "998812"}
    class FakeStore:
        def public_detail(self, profile_id):
            if profile_id != 12:
                raise LocalProfileError("Conta não encontrada.")
            return dict(profile)
    monkeypatch.setattr(launcher_module, "LocalProfileStore", FakeStore)

    app = AccountLauncher()
    requests = Queue(maxsize=1)
    stop = threading.Event()
    app._running[12] = {"requests": requests, "stop": stop}
    app._results[12] = {"state": "ready"}
    page = StubPage()
    worker_done = threading.Event()

    def worker():
        if not worker_done.wait(0.04):
            app._process_browser_request(12, account_id, requests, page)

    background = threading.Thread(target=worker)
    background.start()
    result = app.check_pix_browser_session(12, account_id, timeout=2)
    background.join(2)
    assert result["state"] == "api-reachable"
    assert page.evaluations == 1
    with pytest.raises(LocalProfileError):
        app.check_pix_browser_session(12, bad_id, timeout=0.1)
    job_result = Queue(maxsize=1)
    requests.put({"accountId": bad_id, "profileId": 12, "result": job_result})
    app._process_browser_request(12, account_id, requests, page)
    assert job_result.get_nowait()["state"] == "account-mismatch"
    assert page.evaluations == 1


def test_browser_check_requires_verified_identity_and_running_session(monkeypatch):
    uid = str(uuid4())
    profile = {"id": 22, "accountId": uid, "platformUserId": None}
    class FakeStore:
        def public_detail(self, profile_id):
            return dict(profile)
    monkeypatch.setattr(launcher_module, "LocalProfileStore", FakeStore)
    app = AccountLauncher()
    assert app.check_pix_browser_session(22, uid)["state"] == "unverified-id"
    profile["platformUserId"] = "78911"
    assert app.check_pix_browser_session(22, uid)["state"] == "not-running"
    app._running[22] = {"requests": Queue(maxsize=1), "stop": threading.Event()}
    app._results[22] = {"state": "ready"}
    assert app.check_pix_browser_session(22, uid, timeout=0.01)["state"] == "timeout"


def test_observed_id_permits_only_readonly_probe_and_never_promotes_identity(monkeypatch):
    """Observed candidate can diagnose network status without bypassing ID checks."""
    uid = str(uuid4())
    profile = {
        "id": 31, "accountId": uid, "platformUserId": None,
        "platformObservedUserId": "123987",
    }

    class FakeStore:
        def public_detail(self, profile_id):
            assert profile_id == 31
            return dict(profile)

    monkeypatch.setattr(launcher_module, "LocalProfileStore", FakeStore)
    app = AccountLauncher()
    requests = Queue(maxsize=1)
    app._running[31] = {"requests": requests, "stop": threading.Event()}
    app._results[31] = {"state": "ready"}
    page = StubPage()
    import time

    def worker():
        time.sleep(0.04)
        app._process_browser_request(31, uid, requests, page)

    thread = threading.Thread(target=worker)
    thread.start()
    answer = app.check_pix_browser_session(31, uid, timeout=2)
    thread.join(2)
    assert answer["state"] == "api-reachable"
    assert answer["identityStatus"] == "observed"
    assert "ainda não verificado" in answer["message"]
    assert "cadastro Pix permanece bloqueado" in answer["message"]
    assert profile["platformUserId"] is None
    assert page.evaluations == 1
    with pytest.raises(LocalProfileError):
        app.check_pix_browser_session(31, str(uuid4()), timeout=0.1)
