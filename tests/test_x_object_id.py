"""Passive 5gbet x-object-id header tests -- OFFLINE synthetic responses only.

The manually observed header x-object-id.uid is a source of CANDIDATE IDs.
A synthetic independent authenticated-identity response in this suite is
invented solely to exercise the validator; no actual platform API contract
is asserted.
"""
import json
from types import SimpleNamespace

import pytest

from Settings.platform_identity import (
    FiveGBetIdentityContract, PlatformIdentityObserver,
    parse_x_object_id_uid, provider_identity_contract,
)
from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore


PAGE = "https://www.5gbet.com"
API = "https://cdnapiif.ppa5g.net/hall/api/message/list/all?siteCode=296"
SOURCE = "5gbet:x-object-id.uid:authenticated"


class Page:
    url = PAGE + "/register"

    def __init__(self, context=None):
        self.listeners = {"request": [], "response": []}
        self.context = context if context is not None else self

    def on(self, name, fn):
        self.listeners[name].append(fn)

    def remove_listener(self, name, fn):
        self.listeners[name].remove(fn)

    def emit(self, name, obj):
        for fn in list(self.listeners[name]):
            fn(obj)

    def wait_for_timeout(self, milliseconds):
        pass


class Request:
    def __init__(self, page, uid=None, *, url=API, method="GET", header=None):
        self.url = url
        self.method = method
        self.frame = SimpleNamespace(page=page)
        self.calls = 0
        self.headers = (
            header if header is not None else
            {"X-ObJeCt-Id": json.dumps({"uid":uid, "browserLanguage":"en", "init":{}})}
        )

    def all_headers(self):
        self.calls += 1
        return self.headers


class Response:
    def __init__(self, request, body=None, status=200):
        self.request = request
        self.url = request.url
        self.status = status
        self.body = body if body is not None else {"err_code":0}

    def json(self):
        return self.body

    def finished(self):
        return None


class ReviewedSyntheticFiveGBet(FiveGBetIdentityContract):
    def __init__(self, owners):
        self.owners = owners

    def verify_header_identity(self, uid, response, body, account):
        # Fake independently authenticated response; NOT an actual 5gbet
        # response field or schema. Checks both identity and owner.
        independent = body.get("synthetic_authenticated") if isinstance(body, dict) else None
        return independent == {
            "uid": uid,
            "accountId": account["accountId"],
            "runId": account["runId"],
        }

    def validate_identity(self, uid, source, account):
        return (
            source == SOURCE
            and self.owners.get(account["accountId"]) == (
                account["profileId"], account["runId"], uid
            )
        )


@pytest.fixture
def store(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    return LocalProfileStore(tmp_path / "Nexora" / "generated_profiles.json")


def make(store, source, run):
    p = store.create(source, device_mode="android", start_url=PAGE + "/register")
    store.record_session(p["id"], "starting", run)
    return p


def start(page, profile, run, contract=None):
    observer = PlatformIdentityObserver(
        page, lambda _: None, {**profile, "runId":run},
        expected_origin=PAGE, contract=contract,
    )
    observer.start()
    return observer


def send(page, uid, *, body=None, status=200, context=False, **kwargs):
    req = Request(page, uid, **kwargs)
    if context:
        page.context.emit("request", req)
    else:
        page.emit("request", req)
    res = Response(req, body=body, status=status)
    if context:
        page.context.emit("response", res)
    else:
        page.emit("response", res)
    return req


def auth_body(profile, run, uid):
    return {
        "err_code":0,
        "synthetic_authenticated":{
            "uid":str(uid), "accountId":profile["accountId"], "runId":run
        },
    }


def save(store, p, run, identity):
    return store.save_verified_platform_id(
        p["id"], p["accountId"], run,
        site_origin=PAGE, platform_user_id=identity.user_id,
        platform_id_source=identity.source, identity_evidence=identity,
    )


@pytest.mark.parametrize("raw", [
    None, "", "not-json", "[1,2,3]", "{}", '{"uid":null}',
    '{"uid":true}', '{"uid":false}', '{"uid":0}', '{"uid":-1}',
    '{"uid":1.0}', '{"uid":"772104782"}', '{"uid":9007199254740992}',
])
def test_header_parser_discards_invalid_values(raw):
    assert parse_x_object_id_uid(raw) is None


def test_integer_header_parsing_preserves_digits_without_fixed_id():
    assert parse_x_object_id_uid('{"uid":772104782,"init":{}}') == "772104782"
    assert parse_x_object_id_uid('{"uid":1000000001}') == "1000000001"
    assert parse_x_object_id_uid('{"uid":123}') == "123"


def test_builtin_contract_restricted_to_official_origin():
    assert isinstance(provider_identity_contract(PAGE), FiveGBetIdentityContract)
    assert isinstance(provider_identity_contract("https://5gbet.com"), FiveGBetIdentityContract)
    assert provider_identity_contract("https://fake-5gbet.com") is None


def test_real_header_only_is_candidate_never_verified_or_saved(store):
    p = make(store, "mae", "run79")
    page = Page()
    watcher = start(page, p, "run79")  # actual builtin contract
    try:
        request = send(page, 772104782, body={"err_code":0})
        assert request.calls == 1
        store.mark_registration_clicked(p["id"], p["accountId"], "run79", site_origin=PAGE)
        watcher.after_registration_click()
        assert watcher.identify(wait_seconds=0) is None
        assert store.get(p["id"])["platformUserId"] is None
        assert store.get(p["id"])["status"] == "created"
    finally:
        watcher.close()
    assert not page.listeners["request"] and not page.listeners["response"]


def test_only_exact_api_host_get_and_path_read_sensitive_header(store):
    p = make(store, "mae", "run79")
    page = Page()
    watcher = start(page, p, "run79")
    try:
        variants = [
            ("https://cdnapiif.ppa5g.net.evil.test/hall/api/message/list/all", "GET"),
            ("https://other.ppa5g.net/hall/api/message/list/all", "GET"),
            (PAGE + "/hall/api/message/list/all", "GET"),
            (API, "POST"),
            ("https://cdnapiif.ppa5g.net/hall/api/member/register", "GET"),
        ]
        for url, method in variants:
            request = send(page, 12345, url=url, method=method)
            assert request.calls == 0
        assert watcher._header_seen_uid is None
        request = send(page, 54321)
        assert request.calls == 1
        assert watcher._header_seen_uid == "54321"
    finally:
        watcher.close()


def test_invalid_header_and_absent_header_do_not_create_candidates(store):
    p = make(store, "filha", "run80")
    page = Page()
    watcher = start(page, p, "run80")
    try:
        for header in ({}, {"x-object-id": "{"}, {"x-object-id":'{"uid":true}'}):
            request = send(page, None, header=header)
            assert request.calls == 1
        assert watcher._header_seen_uid is None
        assert watcher._header_requests == {}
    finally:
        watcher.close()


def test_response_errors_not_auth_confirmation_even_with_synthetic_adapter(store):
    p = make(store, "mae", "run79")
    adapter = ReviewedSyntheticFiveGBet({
        p["accountId"]:(p["id"],"run79","111111")
    })
    page = Page()
    watcher = start(page, p, "run79", adapter)
    try:
        store.mark_registration_clicked(p["id"],p["accountId"],"run79",site_origin=PAGE)
        watcher.after_registration_click()
        send(page, 111111, status=403, body=auth_body(p, "run79", "111111"))
        send(page, 111111, body={
            **auth_body(p,"run79","111111"), "err_code":41000,
        })
        send(page, 111111, body={"err_code":0,"success":True})
        assert watcher.identify(wait_seconds=0) is None
        assert store.get(p["id"])["platformUserId"] is None
    finally:
        watcher.close()


def test_two_accounts_own_ids_persist_reopen_without_status_change(store):
    a = make(store, "mae", "run79")
    b = make(store, "filha", "run80")
    adapter = ReviewedSyntheticFiveGBet({
        a["accountId"]:(a["id"],"run79","772104782"),
        b["accountId"]:(b["id"],"run80","772104783"),
    })
    pages = [Page(),Page()]
    watches = [start(pages[0],a,"run79",adapter),start(pages[1],b,"run80",adapter)]
    try:
        for p,run,page,watch,uid in (
            (a,"run79",pages[0],watches[0],772104782),
            (b,"run80",pages[1],watches[1],772104783),
        ):
            store.mark_registration_clicked(p["id"],p["accountId"],run,site_origin=PAGE)
            watch.after_registration_click()
            send(page,uid,body=auth_body(p,run,uid))
            identity = watch.identify(wait_seconds=0)
            assert identity.user_id == str(uid)
            assert identity.profile_id == p["id"]
            save(store,p,run,identity)
    finally:
        for watch in watches:
            watch.close()
    reopened = LocalProfileStore(store.path)
    assert reopened.public_detail(a["id"])["platformUserId"] == "772104782"
    assert reopened.public_detail(b["id"])["platformUserId"] == "772104783"
    for p in (a,b):
        state = reopened.public_detail(p["id"])
        assert state["status"] == "created"
        assert state["registrationReviewRequired"] is True
        assert state["registrationConfirmedAt"] is None
        assert state["platformIdSource"] == SOURCE
        assert state["platformIdVerifiedAt"]


def test_mixed_ids_are_rejected_even_when_each_independently_accepted(store):
    p = make(store, "mae", "run79")
    adapter = ReviewedSyntheticFiveGBet({
        p["accountId"]:(p["id"],"run79","111")
    })
    page=Page()
    watcher=start(page,p,"run79",adapter)
    try:
        store.mark_registration_clicked(p["id"],p["accountId"],"run79",site_origin=PAGE)
        watcher.after_registration_click()
        send(page,111,body=auth_body(p,"run79",111))
        send(page,222,body=auth_body(p,"run79",222))
        assert watcher.identify(wait_seconds=0) is None
        assert store.get(p["id"])["platformUserId"] is None
    finally:
        watcher.close()


def test_context_request_from_different_page_cannot_leak_id(store):
    p=make(store,"mae","run79")
    context=Page()
    owner=Page(context=context)
    unrelated=Page(context=context)
    watcher=start(owner,p,"run79")
    try:
        foreign=Request(unrelated,111)
        context.emit("request",foreign)
        assert foreign.calls == 0
        own=Request(owner,222)
        context.emit("request",own)
        assert own.calls == 1
        assert watcher._header_seen_uid == "222"
    finally:
        watcher.close()
    assert not context.listeners["request"]
    assert not context.listeners["response"]


def test_late_old_run_cannot_overwrite_current_profile(store):
    p=make(store,"mae","run79")
    adapter=ReviewedSyntheticFiveGBet({
        p["accountId"]:(p["id"],"run79","111")
    })
    page=Page()
    watcher=start(page,p,"run79",adapter)
    try:
        store.mark_registration_clicked(p["id"],p["accountId"],"run79",site_origin=PAGE)
        watcher.after_registration_click()
        send(page,111,body=auth_body(p,"run79",111))
        identity=watcher.identify(wait_seconds=0)
        store.record_session(p["id"],"starting","run-next")
        with pytest.raises(LocalProfileError,match="expirada"):
            save(store,p,"run79",identity)
    finally:
        watcher.close()
