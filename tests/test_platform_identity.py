"""Synthetic, offline-only tests. No production identity contract is implied."""
from types import SimpleNamespace
import threading

from Settings.platform_identity import (
    PlatformIdentityObserver, VerifiedPlatformIdentity, site_origin,
)


class Contract:
    origin = "https://platform.example.test"
    identity_response_routes = {("GET", "/api/me")}
    identity_storage_fields = ()
    identity_profile_fields = ()

    def validate_identity(self, user_id, source, account):
        # Only a verified mapping may accept a remote identifier for an owner.
        expected = {"user79": "000079", "user80": "000080"}
        return (source in ("synthetic-registration", "synthetic-authenticated",
                           "synthetic-storage", "synthetic-profile")
                and expected.get(account["username"]) == user_id)

    def identity_from_response(self, url, status, payload, account):
        if status != 200 or not isinstance(payload, dict):
            return None
        return payload.get("remote_member_number"), "synthetic-authenticated"


class FakeResponse:
    def __init__(self, url, payload, method="GET"):
        self.url = url
        self.request = SimpleNamespace(method=method)
        self.status = 200
        self.payload = payload

    def finished(self):
        return None

    def json(self):
        return self.payload


class Locator:
    def __init__(self, value):
        self.value = value

    def count(self):
        return 1

    def inner_text(self, timeout=None):
        return self.value

    def get_attribute(self, attr, timeout=None):
        return self.value


class Page:
    def __init__(self, origin="https://platform.example.test"):
        self.url = origin + "/register"
        self.context = self
        self.listeners = []
        self.pending = []
        self.storage_result = None
        self.profile_result = None
        self.storage_calls = 0

    def on(self, event, fn):
        self.listeners.append(fn)

    def remove_listener(self, event, fn):
        self.listeners.remove(fn)

    def emit(self, response):
        for fn in list(self.listeners):
            fn(response)

    def wait_for_timeout(self, ms):
        if self.pending:
            self.emit(self.pending.pop(0))

    def evaluate(self, script, spec):
        self.storage_calls += 1
        return self.storage_result

    def locator(self, selector):
        return Locator(self.profile_result)


def account(profile_id, username, run_id):
    return {"id": profile_id, "username": username,
            "accountId": "local-" + str(profile_id), "runId": run_id}


def confirmed(remote_id=None):
    return SimpleNamespace(
        status="created", confirmation_source="synthetic-confirmation",
        platform_user_id=remote_id,
        platform_id_source="synthetic-registration" if remote_id else None,
    )


def new(page, owner, contract=None):
    obs = PlatformIdentityObserver(
        page, lambda _: None, owner, expected_origin=page.url, contract=contract,
    )
    obs.start()
    return obs


def test_two_simultaneous_accounts_keep_own_responses_and_ids():
    a, b = Page(), Page()
    oa, ob = new(a, account(79, "user79", "run-79"), Contract()), new(
        b, account(80, "user80", "run-80"), Contract(),
    )
    try:
        a.emit(FakeResponse("https://platform.example.test/api/me",
                            {"remote_member_number": "000079"}))
        b.emit(FakeResponse("https://platform.example.test/api/me",
                            {"remote_member_number": "000080"}))
        assert oa.identify(registration_verdict=confirmed(), wait_seconds=0) == (
            VerifiedPlatformIdentity("000079", "synthetic-authenticated")
        )
        assert ob.identify(registration_verdict=confirmed(), wait_seconds=0) == (
            VerifiedPlatformIdentity("000080", "synthetic-authenticated")
        )
    finally:
        oa.close()
        ob.close()
    assert not a.listeners and not b.listeners


def test_foreign_origin_and_wrong_owner_are_rejected():
    page = Page()
    watcher = new(page, account(79, "user79", "run-79"), Contract())
    try:
        page.emit(FakeResponse("https://other.test/api/me",
                               {"remote_member_number": "000079"}))
        page.emit(FakeResponse("https://platform.example.test/api/me",
                               {"remote_member_number": "000080"}))
        assert watcher.identify(registration_verdict=confirmed(),
                                wait_seconds=0) is None
    finally:
        watcher.close()


def test_registration_id_has_priority_but_requires_owner_validation():
    page = Page()
    watcher = new(page, account(79, "user79", "run-79"), Contract())
    page.emit(FakeResponse("https://platform.example.test/api/me",
                           {"remote_member_number": "000079"}))
    try:
        assert watcher.identify(
            registration_verdict=confirmed("000079"), wait_seconds=0
        ) == VerifiedPlatformIdentity("000079", "synthetic-registration")
        assert watcher.identify(
            registration_verdict=confirmed("000080"), wait_seconds=0
        ) == VerifiedPlatformIdentity("000079", "synthetic-authenticated")
    finally:
        watcher.close()


def test_no_contract_no_id_and_no_storage_read():
    page = Page()
    watcher = new(page, account(79, "user79", "run-79"))
    try:
        page.emit(FakeResponse("https://platform.example.test/api/me",
                               {"remote_member_number": "000079"}))
        assert watcher.identify(
            registration_verdict=confirmed("000079"), wait_seconds=0
        ) is None
        assert page.storage_calls == 0
    finally:
        watcher.close()


def test_unconfirmed_signup_never_extracts_identity():
    page = Page()
    contract = Contract()
    contract.identity_storage_fields = (
        {"area":"local", "key":"audit", "path":["uid"], "source":"synthetic-storage"},
    )
    page.storage_result = "000079"
    watcher = new(page, account(79, "user79", "run-79"), contract)
    try:
        assert watcher.identify(
            registration_verdict=SimpleNamespace(
                status="unconfirmed", confirmation_source=None
            ), wait_seconds=0
        ) is None
        assert page.storage_calls == 0
    finally:
        watcher.close()


def test_preconfigured_storage_leaf_then_official_profile():
    page = Page()
    contract = Contract()
    contract.identity_storage_fields = (
        {"area":"session", "key":"verified-user", "path":["profile", "id"],
         "source":"synthetic-storage"},
    )
    contract.identity_profile_fields = (
        {"selector":"[data-verified-user-id]", "attribute":"text",
         "source":"synthetic-profile"},
    )
    watcher = new(page, account(79, "user79", "run-79"), contract)
    try:
        page.storage_result = "000079"
        assert watcher.identify(
            registration_verdict=confirmed(), wait_seconds=0
        ) == VerifiedPlatformIdentity("000079", "synthetic-storage")
        page.storage_result = "000080"  # wrong account
        page.profile_result = "000079"
        assert watcher.identify(
            registration_verdict=confirmed(), wait_seconds=0
        ) == VerifiedPlatformIdentity("000079", "synthetic-profile")
    finally:
        watcher.close()


def test_responses_without_audited_method_and_path_are_not_parsed():
    class GuardContract(Contract):
        def identity_from_response(self, url, status, payload, account):
            raise AssertionError("Should not parse unexpected endpoint")
    page = Page()
    watcher = new(page, account(79, "user79", "run-79"), GuardContract())
    try:
        page.emit(FakeResponse("https://platform.example.test/hall/api/member/user/info",
                               {"uid":"000079"}))
        assert watcher.identify(
            registration_verdict=confirmed(), wait_seconds=0
        ) is None
    finally:
        watcher.close()


def test_wrong_origin_disables_adapter():
    page = Page()
    watcher = PlatformIdentityObserver(
        page, lambda _: None, account(79, "user79", "run-79"),
        expected_origin="https://another.example.test", contract=Contract(),
    )
    watcher.start()
    try:
        assert watcher.identify(registration_verdict=confirmed("000079"),
                                wait_seconds=0) is None
    finally:
        watcher.close()


def test_origin_normalization():
    assert site_origin("https://platform.example.test:443/profile") == (
        "https://platform.example.test"
    )
    assert site_origin("https://platform.example.test:8443/profile") == (
        "https://platform.example.test:8443"
    )
    assert site_origin("about:blank") == ""
