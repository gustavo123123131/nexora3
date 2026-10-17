"""Offline account-to-Pix persistence, dispatcher and CSRF regression tests."""
from types import SimpleNamespace
from uuid import uuid4
import threading
import time
import pytest

from nexora_accounts.pix_operations import PixOperationStore, PixPreparationError


def setup_store(tmp_path):
    run = str(uuid4())
    a, b, key = str(uuid4()), str(uuid4()), str(uuid4())
    profiles = {
        11: {"id":11,"accountId":a,"origin":"mae","lastSessionId":run,
             "platformUserId":"772104783","fullName":"Marina Souza", "cpf":"52998224725",
             "financialRealName":"Maria Silva",
             "financialCpf":"52998224725","financialIdentitySource":"operator-confirmed",
             "hasWithdrawPin":True},
        12: {"id":12,"accountId":b,"origin":"filha","lastSessionId":run,
             "platformUserId":"772104784","fullName":"Ana Ribeiro", "cpf":"52998224725",
             "financialRealName":"Ana Silva",
             "financialCpf":"52998224725","financialIdentitySource":"operator-confirmed",
             "hasWithdrawPin":True},
    }
    keys = [{"id":key,"type":"PHONE","value":"11987654321","updatedAt":"t1"}]
    detail=SimpleNamespace(public_detail=lambda identifier:dict(profiles[identifier]))
    key_store=SimpleNamespace(list=lambda:{"keys":keys})
    store=PixOperationStore(tmp_path/"pix-operations.json",
                           profile_store=detail,key_store=key_store)
    return store, profiles, keys, run


def test_remote_attempt_isolated_persisted_and_never_leaks_credentials(tmp_path):
    store, profiles, keys, run=setup_store(tmp_path)
    key=keys[0]["id"]
    first=store.begin_remote(profile_id=11, account_id=profiles[11]["accountId"],
                             key_id=key,key_type="PHONE",run_id=run)
    assert first["reused"] is False
    op=first["operation"]
    assert op["status"]=="queued"
    other=store.begin_remote(profile_id=12, account_id=profiles[12]["accountId"],
                             key_id=key,key_type="PHONE",run_id=run)
    assert other["operation"]["operationId"]!=op["operationId"]
    assert other["operation"]["platformIdentityRef"]!=op["platformIdentityRef"]
    assert store.begin_remote(profile_id=11,account_id=profiles[11]["accountId"],
                             key_id=key,key_type="PHONE",run_id=run)["reused"]
    with pytest.raises(PixPreparationError):
        store.get_remote(op["operationId"],profile_id=12,
                         account_id=profiles[12]["accountId"],run_id=run)
    completed=store.finish_remote(op["operationId"],profile_id=11,
        account_id=profiles[11]["accountId"],run_id=run,
        result={"state":"confirmed","stage":"withdrawInfoV2-depois"})
    assert completed["status"]=="confirmed"
    assert completed["externalRequestSent"] is True
    again=store.finish_remote(op["operationId"],profile_id=11,
        account_id=profiles[11]["accountId"],run_id=run,
        result={"state":"failed","stage":"site"})
    assert again["status"]=="confirmed"
    import json
    raw=(tmp_path/"pix-operations.json").read_text()
    for sensitive in ("11987654321","Maria Silva","52998224725","772104783","387520"):
        assert sensitive not in raw
    assert store.list(11)["operations"][0]["status"]=="confirmed"


@pytest.mark.parametrize("change,expected", [
    ({"financialRealName":None,"financialCpf":None,
      "fullName":None},"Nome completo"),
    ({"financialRealName":None,"financialCpf":None,
      "cpf":None},"CPF"),
    ({"platformUserId":None,"platformObservedUserId":None},"ID da plataforma"),
])
def test_specific_missing_saved_detail_reports_field(tmp_path,change,expected):
    store, profiles, keys, run=setup_store(tmp_path)
    profiles[11].update(change)
    with pytest.raises(PixPreparationError,match=expected):
        store.begin_remote(profile_id=11,account_id=profiles[11]["accountId"],
                           key_id=keys[0]["id"],key_type="PHONE",run_id=run)
    assert store.list()["operations"]==[]


def test_pix_bind_route_requires_csrf_and_selected_session(monkeypatch):
    from fastapi.testclient import TestClient
    import servidor
    from nexora_accounts.session_launcher import ACCOUNT_LAUNCHER
    from nexora_accounts.pix_operations import PixPreparationError
    calls=[]
    def fake(profile,account,key,kind):
        calls.append((profile,account,key,kind))
        raise PixPreparationError("Abra o Chromium desta conta para cadastrar a chave Pix.")
    monkeypatch.setattr(ACCOUNT_LAUNCHER,"bind_pix_key",fake)
    body={"profileId":11,"accountId":str(uuid4()),
          "keyId":str(uuid4()),"keyType":"PHONE"}
    with TestClient(servidor.app) as client:
        assert client.post("/api/operations/pix/bind",json=body).status_code==403
        accepted=client.post("/api/operations/pix/bind",json=body,
            headers={"X-LixoCPA-CSRF":servidor._CSRF_TOKEN})
        assert accepted.status_code==422
        assert "Abra o Chromium" in accepted.json()["detail"]
        assert calls==[(11,body["accountId"],body["keyId"],"PHONE")]


def test_launcher_routes_to_only_live_matching_account_thread(monkeypatch,tmp_path):
    import nexora_accounts.session_launcher as launcher_module
    import nexora_accounts.pix_operations as pix_mod
    from nexora_accounts.browser_sessions import claim_account, mark_browser_ready
    store, profiles, keys, run=setup_store(tmp_path)
    monkeypatch.setattr(pix_mod,"PixOperationStore",lambda:store)
    monkeypatch.setattr(launcher_module,"LocalProfileStore",
                        lambda:SimpleNamespace(public_detail=lambda id:profiles[id]))
    launched=launcher_module.AccountLauncher()
    stop=threading.Event()
    p=profiles[11]
    with claim_account("mae",11,account_uuid=p["accountId"],
                       stop_event=stop,run_id=run) as session:
        mark_browser_ready(session)
        # Mock remote provider response, without performing any network call.
        def fake_bind(**kwargs):
            return store.finish_remote(kwargs["operation_id"],profile_id=11,
                       account_id=p["accountId"],run_id=run,
                       result={"state":"confirmed","stage":"withdrawInfoV2-depois"})
        monkeypatch.setattr(launched,"_bind_pix_on_owner_thread",fake_bind)
        outputs=[]
        thread=threading.Thread(target=lambda: outputs.append(
            launched.bind_pix_key(11,p["accountId"],keys[0]["id"],"PHONE",timeout=2)))
        thread.start()
        for _ in range(200):
            if not session["requests"].empty():
                break
            time.sleep(.005)
        assert not session["requests"].empty()
        launched._process_browser_request(11,p["accountId"],session["requests"],
                                         object(),run_id=run)
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert outputs[0]["operation"]["status"]=="confirmed"
        with pytest.raises(PixPreparationError,match="Conta selecionada divergente"):
            launched.bind_pix_key(11,profiles[12]["accountId"],keys[0]["id"],"PHONE")


def test_saved_profile_name_and_cpf_work_without_financial_confirmation(tmp_path):
    """No manual second confirmation is needed for already saved detail fields."""
    store, profiles, keys, run = setup_store(tmp_path)
    current = profiles[11]
    current.update(financialRealName=None, financialCpf=None,
                   financialIdentitySource=None,
                   platformUserId=None, platformObservedUserId="772104783")
    op = store.begin_remote(
        profile_id=11, account_id=current["accountId"],
        key_id=keys[0]["id"], key_type="PHONE", run_id=run
    )["operation"]
    assert op["status"] == "queued"
    assert op["identityStatus"] == "observed"
    # Even without a separate "financial owner" record, the saved profile
    # name/CPF remain accessible without copying them into operation history.
    history = (tmp_path / "pix-operations.json").read_text("utf-8")
    assert "Marina Souza" not in history
    assert "52998224725" not in history


def test_both_owner_fields_are_resolved_from_the_same_saved_pair():
    from nexora_accounts.pix_operations import pix_owner_from_details
    profile = {
        "fullName": "Marina Souza", "cpf": "52998224725",
        "financialRealName": "Carolina Ribeiro", "financialCpf": "529.982.247-25",
        "financialIdentitySource": "operator-confirmed",
    }
    assert pix_owner_from_details(profile) == ("Carolina Ribeiro", "52998224725")
    profile["financialCpf"] = None
    # Never combine a financial name with the profile CPF.
    assert pix_owner_from_details(profile) == ("Marina Souza", "52998224725")


def test_browser_worker_passes_saved_profile_name_and_cpf_without_extra_gate(
        tmp_path, monkeypatch):
    import nexora_accounts.pix_operations as operations
    import nexora_accounts.pix_key_store as key_module
    import nexora_accounts.pix_browser_bind as browser_module
    import nexora_accounts.session_launcher as launcher_module

    store, profiles, keys, run = setup_store(tmp_path)
    p = profiles[11]
    p.update(financialRealName=None, financialCpf=None,
             financialIdentitySource=None)
    reservation = store.begin_remote(
        profile_id=11, account_id=p["accountId"],
        key_id=keys[0]["id"], key_type="PHONE", run_id=run
    )
    calls = []
    class SavedAccountStore:
        def public_detail(self, pid):
            assert pid == 11
            return dict(p)
        def read_withdraw_pin(self, pid):
            raise AssertionError("Pix não deve exigir PIN salvo por conta")
    monkeypatch.setattr(launcher_module, "LocalProfileStore", SavedAccountStore)
    monkeypatch.setattr(operations, "PixOperationStore", lambda: store)
    monkeypatch.setattr(key_module, "PixKeyStore", lambda: store.keys)
    def fake_bind(page, *, name, cpf, pin, key, kind):
        calls.append((page, name, cpf, pin, key, kind))
        return {"state":"confirmed", "stage":"withdrawInfoV2-depois"}
    monkeypatch.setattr(browser_module, "bind_in_page", fake_bind)
    page=object()
    saved = launcher_module.AccountLauncher()._bind_pix_on_owner_thread(
        profile_id=11, account_id=p["accountId"], run_id=run,
        operation_id=reservation["operation"]["operationId"],page=page,
    )
    assert saved["status"] == "confirmed"
    assert calls == [(page, "Marina Souza", "52998224725",
                      "387520", "11987654321", "PHONE")]


def test_pin_387520_is_available_for_all_accounts_even_without_saved_vault(tmp_path):
    from nexora_accounts.pix_operations import PIX_WITHDRAW_PIN
    store, profiles, keys, run = setup_store(tmp_path)
    assert PIX_WITHDRAW_PIN == "387520"
    for identifier in (11, 12):
        account = profiles[identifier]
        account["hasWithdrawPin"] = False
        first = store.begin_remote(
            profile_id=identifier, account_id=account["accountId"],
            key_id=keys[0]["id"], key_type="PHONE", run_id=run,
        )
        assert first["operation"]["status"] == "queued"
        assert first["operation"]["accountId"] == account["accountId"]
    records = (tmp_path / "pix-operations.json").read_text(encoding="utf-8")
    assert "387520" not in records
