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
             "platformUserId":"772104783","financialRealName":"Maria Silva",
             "financialCpf":"52998224725","financialIdentitySource":"operator-confirmed",
             "hasWithdrawPin":True},
        12: {"id":12,"accountId":b,"origin":"filha","lastSessionId":run,
             "platformUserId":"772104784","financialRealName":"Ana Silva",
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
    for sensitive in ("11987654321","Maria Silva","52998224725","772104783","000007"):
        assert sensitive not in raw
    assert store.list(11)["operations"][0]["status"]=="confirmed"


@pytest.mark.parametrize("change,expected", [
    ({"financialRealName":None},"Nome real"),
    ({"financialCpf":None},"CPF real"),
    ({"financialIdentitySource":None},"Nome real"),
    ({"hasWithdrawPin":False},"PIN de saque"),
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
