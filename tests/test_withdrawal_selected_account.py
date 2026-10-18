"""Account isolation regression tests for read-only withdrawal operations.

No provider network, PIN, tokens or payout requests are used in these tests.
"""
from types import SimpleNamespace
import threading
import time
from uuid import uuid4

import pytest

from nexora_accounts.browser_sessions import claim_account, mark_browser_ready
import nexora_accounts.session_launcher as launcher_module
import nexora_accounts.withdrawal_operations as operations
import nexora_accounts.withdrawal_browser as browser_module


def test_selected_account_owns_all_withdrawal_requests(monkeypatch, tmp_path):
    profiles = {
        91001: {"id": 91001, "accountId": str(uuid4()),
                "origin": "mae", "lastSessionId": str(uuid4())},
        91002: {"id": 91002, "accountId": str(uuid4()),
                "origin": "filha", "lastSessionId": str(uuid4())},
    }
    reader = SimpleNamespace(public_detail=lambda pid: dict(profiles[pid]))
    store = operations.WithdrawalStore(tmp_path / "withdrawals.json", profiles=reader)
    monkeypatch.setattr(operations, "WithdrawalStore", lambda: store)
    monkeypatch.setattr(launcher_module, "LocalProfileStore", lambda: reader)
    requests_seen = []

    def inspect(page):
        requests_seen.append(page)
        return {"stage": "contract",
                "message": "O valor realmente sacável e os campos de envio ainda não foram validados no domínio real.",
                "trace": [{"stage": "withdrawInfoV2", "event": "api_accepted",
                           "httpStatus": 200, "providerCode": 1}]}

    monkeypatch.setattr(browser_module, "inspect_withdrawal_preflight", inspect)
    first, second = profiles[91001], profiles[91002]
    app = launcher_module.AccountLauncher()
    with claim_account("mae", 91001, account_uuid=first["accountId"],
                       stop_event=threading.Event(), run_id=first["lastSessionId"]) as worker_a:
        with claim_account("filha", 91002, account_uuid=second["accountId"],
                           stop_event=threading.Event(), run_id=second["lastSessionId"]) as worker_b:
            mark_browser_ready(worker_a)
            mark_browser_ready(worker_b)
            responses = []
            request_thread = threading.Thread(target=lambda: responses.append(
                app.preflight_withdrawal(91001, first["accountId"], timeout=3)))
            request_thread.start()
            for _ in range(200):
                if not worker_a["requests"].empty():
                    break
                time.sleep(0.005)
            assert not worker_a["requests"].empty()
            assert worker_b["requests"].empty()
            page_a, page_b = object(), object()
            app._process_browser_request(91001, first["accountId"],
                                         worker_a["requests"], page_a,
                                         run_id=first["lastSessionId"])
            request_thread.join(3)
            assert not request_thread.is_alive()
            assert responses[0]["operation"]["accountId"] == first["accountId"]
            assert responses[0]["operation"]["status"] == "blocked"
            assert requests_seen == [page_a]
            assert page_b not in requests_seen
            assert not store.list(91002)["operations"]
            with pytest.raises(operations.WithdrawalPreparationError, match="divergente"):
                app.preflight_withdrawal(91001, second["accountId"], timeout=0.1)
            assert worker_b["requests"].empty()


def test_stale_selected_session_refuses_withdrawal(monkeypatch):
    profile = {"id": 91003, "accountId": str(uuid4()),
               "origin": "mae", "lastSessionId": str(uuid4())}
    monkeypatch.setattr(launcher_module, "LocalProfileStore", lambda:
        SimpleNamespace(public_detail=lambda pid: dict(profile)))
    with claim_account("mae", profile["id"], account_uuid=profile["accountId"],
                       stop_event=threading.Event(), run_id=str(uuid4())) as worker:
        mark_browser_ready(worker)
        with pytest.raises(operations.WithdrawalPreparationError,
                           match="não corresponde"):
            launcher_module.AccountLauncher().preflight_withdrawal(
                profile["id"], profile["accountId"], timeout=0.1)
        assert worker["requests"].empty()
