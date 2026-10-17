"""Offline checks: observed IDs are NOT authenticated identity evidence."""
import pytest
from types import SimpleNamespace
from Settings.platform_identity import (
    PlatformIdentityObserver, ObservedPlatformIdentity, provider_identity_contract,
)
from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError

SITE="https://www.5gbet.com"
API_CO="https://cdnapiif.ppa5g.co/hall/api/message/list/all"
API_NET="https://cdnapiif.ppa5g.net/hall/api/message/list/all"

class Page:
    def __init__(self, storage=None):
        self.url=SITE+"/"
        self.context=self
        self.listeners={}
        self.storage=storage or []
    def on(self,event,fn): self.listeners.setdefault(event,[]).append(fn)
    def remove_listener(self,event,fn): self.listeners[event].remove(fn)
    def emit(self,event,obj):
        for cb in list(self.listeners.get(event,())): cb(obj)
    def evaluate(self,script): return self.storage

class Request:
    def __init__(self,page,url,uid):
        self.url=url
        self.method="GET"
        self.frame=SimpleNamespace(page=page)
        self.uid=uid
        self.headers_calls=0
    def all_headers(self):
        self.headers_calls+=1
        return {"x-object-id":'{"uid":'+str(self.uid)+'}'}

class Response:
    def __init__(self,request):
        self.request=request
        self.url=request.url
        self.status=200
    def json(self): return {"err_code":0}
    def finished(self): return None

def emit(page,url,uid):
    request=Request(page,url,uid)
    page.emit("request",request)
    page.emit("response",Response(request))
    return request

@pytest.fixture
def store(tmp_path,monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae,"PROFILES_DIR",tmp_path/"mae")
    monkeypatch.setattr(filha,"PROFILES_DIR",tmp_path/"filha")
    return LocalProfileStore(tmp_path/"profiles.json")

def create(store, origin, run):
    p=store.create(origin,device_mode="android",start_url=SITE+"/")
    store.record_session(p["id"],"starting",run)
    return p

def watch(page,profile,run):
    watcher=PlatformIdentityObserver(page,lambda msg:None,
        {**profile,"runId":run},expected_origin=SITE)
    watcher.start()
    return watcher

def save(store,p,run,identity):
    return store.save_observed_platform_id(p["id"],p["accountId"],run,
        site_origin=SITE,observed_identity=identity)

@pytest.mark.parametrize("api",[API_CO,API_NET])
def test_both_observed_hosts_preserve_unverified_status(store,api):
    p=create(store,"mae","run-a"); page=Page(); o=watch(page,p,"run-a")
    try:
        req=emit(page,api,772104783)
        result=o.observe_candidate()
        assert req.headers_calls==1
        assert isinstance(result,ObservedPlatformIdentity)
        assert result.user_id=="772104783"
        assert result.source.endswith(":observed")
        save(store,p,"run-a",result)
        detail=LocalProfileStore(store.path).public_detail(p["id"])
        assert detail["platformObservedUserId"]=="772104783"
        assert detail["platformUserId"] is None
        assert detail["platformIdVerifiedAt"] is None
        assert detail["status"]=="pending"
        assert o.identify(wait_seconds=0) is None
    finally:o.close()

def test_unknown_hosts_never_inspected(store):
    p=create(store,"mae","run-a");page=Page();o=watch(page,p,"run-a")
    try:
        req=emit(page,API_CO.replace("ppa5g.co","ppa5g.co.evil.invalid"),111)
        assert o.observe_candidate() is None
        assert req.headers_calls==0
    finally:o.close()

def test_storage_only_late_observation(store):
    p=create(store,"filha","run-b");page=Page();o=watch(page,p,"run-b")
    try:
        assert o.observe_candidate() is None
        page.storage=[{"uid":"883104783","area":"session","key":"sessionData",
            "path":["user","uid"],"kind":"json"}]
        o._storage_last_probe=0
        result=o.observe_candidate()
        assert result.user_id=="883104783"
        assert result.source=="5gbet:storage:observed"
        save(store,p,"run-b",result)
        assert store.public_detail(p["id"])["platformUserId"] is None
    finally:o.close()

def test_sessions_do_not_cross_and_expired_run_rejected(store):
    a=create(store,"mae","run-a");b=create(store,"filha","run-b")
    pa,pb=Page(),Page()
    wa,wb=watch(pa,a,"run-a"),watch(pb,b,"run-b")
    try:
        emit(pa,API_CO,111111)
        emit(pb,API_NET,222222)
        ia=wa.observe_candidate();ib=wb.observe_candidate()
        assert ia.user_id=="111111" and ib.user_id=="222222"
        with pytest.raises(LocalProfileError):
            save(store,b,"run-b",ia)
        save(store,a,"run-a",ia);save(store,b,"run-b",ib)
        store.record_session(a["id"],"starting","run-next")
        with pytest.raises(LocalProfileError):
            save(store,a,"run-a",ia)
    finally:
        wa.close();wb.close()

def test_conflict_clears_observed_without_touching_status(store):
    p=create(store,"mae","run-a");page=Page();o=watch(page,p,"run-a")
    try:
        emit(page,API_CO,111111);one=o.observe_candidate();save(store,p,"run-a",one)
        emit(page,API_NET,222222)
        assert o.observe_candidate() is None
        assert o.observation_conflict
        store.invalidate_observed_platform_id(p["id"],p["accountId"],"run-a")
        assert store.public_detail(p["id"])["platformObservedUserId"] is None
    finally:o.close()

def test_header_storage_crosscheck_and_click_status_unchanged(store):
    p=create(store,"mae","run-a")
    page=Page([{"uid":"222333","area":"local","key":"account",
                "path":["uid"],"kind":"json"}])
    o=watch(page,p,"run-a")
    try:
        emit(page,API_CO,222333)
        result=o.observe_candidate()
        assert result.source=="5gbet:header+storage:observed"
        store.mark_registration_clicked(p["id"],p["accountId"],"run-a",site_origin=SITE)
        save(store,p,"run-a",result)
        d=store.public_detail(p["id"])
        assert d["status"]=="created"
        assert d["platformUserId"] is None
        assert d["registrationReviewRequired"] is True
    finally:o.close()
