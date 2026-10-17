"""5gbet authenticated current-user evidence: offline-only regressions.

The real provider was NOT contacted. The mocked SDK offers a synthetic
authenticated response with explicit uid+username; responses with only
HTTP 200, code 1, a local claim or a mismatched account must never verify.
"""
from pathlib import Path
import threading
import time
from uuid import uuid4

import pytest

from Settings.platform_identity import (
    ObservedPlatformIdentity, VerifiedPlatformIdentity
)
import nexora_accounts.local_profiles as profiles_module
from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError
from nexora_accounts.browser_sessions import claim_account, mark_browser_ready
import nexora_accounts.session_launcher as launch
import nexora_accounts.platform_identity_probe as provider

ORIGIN = "https://www.5gbet.com"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "Nexora" / "profiles.json"
    monkeypatch.setattr(profiles_module, "_default_path", lambda: path)
    return LocalProfileStore(path)


def existing_account(store, *, uid="772104782", run="test-identity-run"):
    item = store.create("mae", device_mode="android", start_url=ORIGIN)
    store.record_session(item["id"], "starting", run)
    store.mark_registration_clicked(
        item["id"], item["accountId"], run, site_origin=ORIGIN,
    )
    observed = ObservedPlatformIdentity(
        uid, "5gbet:x-object-id.uid:observed",
        item["id"], item["accountId"], run, ORIGIN,
    )
    store.save_observed_platform_id(
        item["id"], item["accountId"], run,
        site_origin=ORIGIN, observed_identity=observed
    )
    return item


class FakePage:
    url = ORIGIN + "/"

    def __init__(self, expected_result="matched"):
        self.expected_result = expected_result
        self.owner_thread = None
        self.calls = []

    def evaluate(self, source, data):
        assert "member/v2/user/info" in source
        assert "bindalipayV3" not in source
        assert "withdraw" not in source.lower()
        self.calls.append(dict(data))
        self.owner_thread = threading.get_ident()
        return {"state": self.expected_result}


def test_probe_rejects_foreign_origin_and_invalid_candidates():
    page = FakePage()
    page.url = "https://other.example/"
    assert provider.check_auth_identity_page(page, observed_uid="123", username="user")["state"] == "wrong-origin"
    page.url = ORIGIN
    assert provider.check_auth_identity_page(page, observed_uid="", username="user")["state"] == "invalid-observed-id"
    assert provider.check_auth_identity_page(page, observed_uid="123", username="")["state"] == "invalid-username"
    assert not page.calls


@pytest.mark.parametrize("response", [
    "schema-unconfirmed", "username-mismatch", "uid-mismatch",
    "provider-rejected", "timeout", "missing-sdk",
])
def test_unconfirmed_provider_responses_never_save_id(store, response):
    account = existing_account(store)
    fake_page = FakePage(response)
    result = launch.AccountLauncher()._verify_identity_on_owner_thread(
        account["id"], account["accountId"], "test-identity-run", fake_page,
    )
    assert result["state"] == response
    assert LocalProfileStore(store.path).public_detail(account["id"])["platformUserId"] is None


def test_authenticated_uid_plus_username_same_live_session_can_persist(store):
    account = existing_account(store)
    app = launch.AccountLauncher()
    stop = threading.Event()
    page = FakePage()
    ready = threading.Event()
    active_owner = {}
    worker = None
    with claim_account("mae", account["id"], account_uuid=account["accountId"],
                       stop_event=stop, run_id="test-identity-run") as live:
        mark_browser_ready(live)
        def process():
            active_owner["thread"] = threading.get_ident()
            ready.set()
            while not stop.is_set():
                app._process_browser_request(
                    account["id"], account["accountId"], live["requests"], page,
                    run_id="test-identity-run",
                )
                time.sleep(0.01)

        worker = threading.Thread(target=process)
        worker.start()
        assert ready.wait(2)
        result = app.verify_platform_identity(
            account["id"], account["accountId"], timeout=3,
        )
        stop.set()
        worker.join(3)
    assert result["state"] == "verified"
    assert page.owner_thread == active_owner["thread"]
    assert page.calls == [{"expectedUid": "772104782", "expectedUsername": account["username"]}]
    saved = LocalProfileStore(store.path).public_detail(account["id"])
    assert saved["platformUserId"] == "772104782"
    assert saved["platformIdSource"] == provider.VERIFICATION_SOURCE
    assert saved["registrationReviewRequired"] is True
    assert saved["registrationConfirmedAt"] is None


def test_mismatched_broker_uuid_and_run_never_query_identity(store):
    account = existing_account(store)
    app = launch.AccountLauncher()
    stop = threading.Event()
    page = FakePage()
    with claim_account("mae", account["id"], account_uuid=account["accountId"],
                       stop_event=stop, run_id="different-run") as live:
        mark_browser_ready(live)
        result = app.verify_platform_identity(account["id"], account["accountId"])
        assert result["state"] == "stale-session"
        with pytest.raises(LocalProfileError):
            app.verify_platform_identity(account["id"], str(uuid4()))
        assert not page.calls
    assert store.public_detail(account["id"])["platformUserId"] is None


def test_provider_identity_evidence_must_match_observed_id_and_session(store):
    account = existing_account(store)
    evidence = VerifiedPlatformIdentity(
        "99999", provider.VERIFICATION_SOURCE,
        profile_id=account["id"], account_id=account["accountId"],
        run_id="test-identity-run", platform_origin=ORIGIN,
    )
    with pytest.raises(LocalProfileError, match="autenticada"):
        store.save_verified_platform_id(
            account["id"], account["accountId"], "test-identity-run",
            site_origin=ORIGIN, platform_user_id="99999",
            platform_id_source=provider.VERIFICATION_SOURCE,
            identity_evidence=evidence,
        )
    assert store.public_detail(account["id"])["platformUserId"] is None


def test_identity_verification_http_route_and_csrf(monkeypatch):
    from fastapi.testclient import TestClient
    import servidor

    received = []
    def fake_verify(profile_id, account_id):
        received.append((profile_id, account_id))
        return {"state": "schema-unconfirmed",
                "message": "A API não trouxe UID/username verificados."}
    monkeypatch.setattr(launch.ACCOUNT_LAUNCHER, "verify_platform_identity", fake_verify)
    account_id = str(uuid4())
    body = {"profileId": 47, "accountId": account_id}
    with TestClient(servidor.app) as client:
        assert client.post("/api/operations/pix/verify-identity", json=body).status_code == 403
        token = client.get("/api/state").json()["csrf_token"]
        result = client.post("/api/operations/pix/verify-identity", json=body,
                             headers={"X-LixoCPA-CSRF": token})
        assert result.status_code == 200
        assert result.json()["state"] == "schema-unconfirmed"
    assert received == [(47, account_id)]


def test_firstparty_js_probe_checks_strict_response_pair_without_network():
    """Run the unmodified browser script against fake first-party SDK assets."""
    from playwright.sync_api import sync_playwright

    def script_handler(route):
        url = route.request.url
        if url.endswith("/assets/commonChunk.demo123.js"):
            route.fulfill(
                status=200, content_type="text/javascript",
                body="""export const n = {
                  request: async ({method,url,data}) => {
                    if(method!=="POST"||url!=="/api/member/v2/user/info"||
                       JSON.stringify(data)!==JSON.stringify({action:[0]}))throw Error("wrong route");
                    return {data:{code:1,data:{basic:window.mockUser}}};
                  }
                };""",
            )
        elif url.startswith(ORIGIN):
            route.fulfill(status=200, content_type="text/html",
                          body="<html><head></head><body>Test-only</body></html>")
        else:
            route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", script_handler)
            page.goto(ORIGIN + "/")
            page.evaluate("""async () => {
              await import('/assets/commonChunk.demo123.js');
            }""")
            page.evaluate("window.mockUser = {uid:'772104782',username:'account9'}")
            assert provider.check_auth_identity_page(page, observed_uid="772104782",
                                                      username="account9")["state"] == "matched"
            page.evaluate("window.mockUser = {uid:'11111',username:'account9'}")
            assert provider.check_auth_identity_page(page, observed_uid="772104782",
                                                      username="account9")["state"] == "uid-mismatch"
            page.evaluate("window.mockUser = {uid:'772104782',username:'someoneelse'}")
            assert provider.check_auth_identity_page(page, observed_uid="772104782",
                                                      username="account9")["state"] == "username-mismatch"
            page.evaluate("window.mockUser = {isTrustedDevice:true}")
            assert provider.check_auth_identity_page(page, observed_uid="772104782",
                                                      username="account9")["state"] == "schema-unconfirmed"
        finally:
            browser.close()
