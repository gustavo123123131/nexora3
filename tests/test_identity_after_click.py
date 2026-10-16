"""Offline-only verification of a REAL-ID-shaped, source-validated identity.

The provider adapter and /api/me route used here are SYNTHETIC. These tests
do not establish a production response format or contact a remote website.
"""
import threading
from types import SimpleNamespace

import pytest

from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore
from Settings.platform_identity import PlatformIdentityObserver, VerifiedPlatformIdentity


ORIGIN = "https://platform.example.test"


class ReviewedSyntheticAdapter:
    origin = ORIGIN
    identity_response_routes = {("GET", "/api/me")}
    identity_storage_fields = ()
    identity_profile_fields = ()

    def __init__(self, owners):
        self.owners = owners

    def identity_from_response(self, url, http_status, payload, account):
        if http_status != 200 or not isinstance(payload, dict):
            return None
        return payload.get("synthetic_member_id"), "synthetic-authenticated-user"

    def validate_identity(self, user_id, source, account):
        # Test-specific oracle confirms both remote ID and internal account
        # ownership. Do not deploy this adapter in production.
        expected = self.owners.get(account["accountId"])
        return (
            source == "synthetic-authenticated-user"
            and expected == (account["profileId"], account["runId"], user_id)
        )


class Page:
    url = ORIGIN + "/register"

    def __init__(self, context=None):
        self.listeners = []
        self.context = context if context is not None else self
        self.events = []

    def on(self, name, fn):
        assert name == "response"
        self.listeners.append(fn)

    def remove_listener(self, name, fn):
        self.listeners.remove(fn)

    def emit(self, response):
        for fn in list(self.listeners):
            fn(response)

    def wait_for_timeout(self, milliseconds):
        if self.events:
            self.emit(self.events.pop(0))


class Response:
    def __init__(self, page, value, *, url=ORIGIN + "/api/me"):
        self.url = url
        self.status = 200
        self.request = SimpleNamespace(method="GET", frame=SimpleNamespace(page=page))
        self.value = value

    def json(self):
        return {"synthetic_member_id": self.value}

    def finished(self):
        return None


@pytest.fixture
def store(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    return LocalProfileStore(tmp_path / "Nexora" / "generated_profiles.json")


def create(store, origin, run):
    profile = store.create(
        origin, device_mode="android", start_url=ORIGIN + "/register"
    )
    store.record_session(profile["id"], "starting", run)
    return profile


def clicked(store, p, run):
    return store.mark_registration_clicked(
        p["id"], p["accountId"], run, site_origin=ORIGIN
    )


def observer(page, profile, run, contract):
    watch = PlatformIdentityObserver(
        page, lambda _: None,
        {**profile, "runId": run},
        expected_origin=ORIGIN,
        contract=contract,
    )
    watch.start()
    return watch


def save(store, profile, run, identity):
    return store.save_verified_platform_id(
        profile["id"], profile["accountId"], run,
        site_origin=ORIGIN,
        platform_user_id=identity.user_id,
        platform_id_source=identity.source,
        identity_evidence=identity,
    )


def test_two_simultaneous_sessions_store_distinct_verified_ids_and_reopen(store):
    a, b = create(store, "mae", "run-a"), create(store, "filha", "run-b")
    owners = {
        a["accountId"]: (a["id"], "run-a", "000042"),
        b["accountId"]: (b["id"], "run-b", "000043"),
    }
    contract = ReviewedSyntheticAdapter(owners)
    pages = [Page(), Page()]
    watches = [
        observer(pages[0], a, "run-a", contract),
        observer(pages[1], b, "run-b", contract),
    ]
    try:
        pages[0].emit(Response(pages[0], "000042"))
        pages[1].emit(Response(pages[1], "000043"))
        clicked(store, a, "run-a")
        clicked(store, b, "run-b")
        ia = watches[0].identify(wait_seconds=0)
        ib = watches[1].identify(wait_seconds=0)
        assert ia.user_id == "000042"
        assert ib.user_id == "000043"
        save(store, a, "run-a", ia)
        save(store, b, "run-b", ib)
    finally:
        for watch in watches:
            watch.close()
    restored = LocalProfileStore(store.path)
    for profile, remote in ((a, "000042"), (b, "000043")):
        row = restored.public_detail(profile["id"])
        assert row["status"] == "created"
        assert row["registrationReviewRequired"] is True
        assert row["registrationConfirmedAt"] is None
        assert row["registrationConfirmationSource"] is None
        assert row["platformUserId"] == remote
        assert row["platformIdSource"] == "synthetic-authenticated-user"
        assert row["platformIdVerifiedAt"]
        assert row["platformOrigin"] == ORIGIN


def test_clicked_account_requires_own_validated_identity_evidence(store):
    a, b = create(store, "mae", "run-a"), create(store, "filha", "run-b")
    owners = {
        a["accountId"]: (a["id"], "run-a", "000001"),
        b["accountId"]: (b["id"], "run-b", "000002"),
    }
    page = Page()
    watch = observer(page, a, "run-a", ReviewedSyntheticAdapter(owners))
    try:
        # Invalid ID from the other account fails independent owner validation.
        page.emit(Response(page, "000002"))
        clicked(store, a, "run-a")
        clicked(store, b, "run-b")
        assert watch.identify(wait_seconds=0) is None
        page.emit(Response(page, "000001"))
        identity = watch.identify(wait_seconds=0)
        assert identity.user_id == "000001"
        with pytest.raises(LocalProfileError, match="independente"):
            store.save_verified_platform_id(
                a["id"], a["accountId"], "run-a",
                site_origin=ORIGIN, platform_user_id="000001",
                platform_id_source=identity.source,
            )
        with pytest.raises(LocalProfileError, match="independente"):
            save(store, b, "run-b", identity)
        with pytest.raises(LocalProfileError, match="Origem"):
            store.save_verified_platform_id(
                a["id"], a["accountId"], "run-a",
                site_origin="https://elsewhere.test",
                platform_user_id=identity.user_id,
                platform_id_source=identity.source,
                identity_evidence=identity,
            )
        assert store.get(a["id"])["platformUserId"] is None
        assert store.get(b["id"])["platformUserId"] is None
        save(store, a, "run-a", identity)
        with pytest.raises(LocalProfileError, match="Conflito"):
            store.save_verified_platform_id(
                a["id"], a["accountId"], "run-a",
                site_origin=ORIGIN, platform_user_id="999999",
                platform_id_source=identity.source,
                identity_evidence=VerifiedPlatformIdentity(
                    "999999", identity.source,
                    profile_id=a["id"], account_id=a["accountId"],
                    run_id="run-a", platform_origin=ORIGIN,
                )
            )
    finally:
        watch.close()


def test_context_response_for_other_tab_same_origin_is_ignored(store):
    a = create(store, "mae", "run-a")
    context = Page()
    page = Page(context=context)
    unrelated = Page(context=context)
    contract = ReviewedSyntheticAdapter(
        {a["accountId"]: (a["id"], "run-a", "000001")}
    )
    watch = observer(page, a, "run-a", contract)
    try:
        # Context-only event must be attributed to a matching frame.page.
        for callback in list(context.listeners):
            callback(Response(unrelated, "000001"))
        assert watch.identify(wait_seconds=0) is None
        for callback in list(context.listeners):
            callback(Response(page, "000001"))
        assert watch.identify(wait_seconds=0).user_id == "000001"
    finally:
        watch.close()


def test_old_run_cannot_persist_late_authenticated_id(store):
    a = create(store, "mae", "run-a")
    clicked(store, a, "run-a")
    page = Page()
    contract = ReviewedSyntheticAdapter({
        a["accountId"]: (a["id"], "run-a", "000001")
    })
    watch = observer(page, a, "run-a", contract)
    page.emit(Response(page, "000001"))
    identity = watch.identify(wait_seconds=0)
    store.record_session(a["id"], "starting", "run-next")
    try:
        with pytest.raises(LocalProfileError, match="expirada"):
            save(store, a, "run-a", identity)
        assert store.get(a["id"])["platformUserId"] is None
    finally:
        watch.close()


def test_unknown_provider_contract_does_not_return_or_persist_remote_id(store):
    a = create(store, "filha", "run-a")
    page = Page()
    watch = observer(page, a, "run-a", None)
    try:
        page.emit(Response(page, "000001"))
        clicked(store, a, "run-a")
        assert watch.identify(wait_seconds=0) is None
        assert store.get(a["id"])["platformUserId"] is None
    finally:
        watch.close()
