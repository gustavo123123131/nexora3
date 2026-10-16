"""Status-only registration tests with an OFFLINE synthetic reviewed adapter.

These scenarios assert the session/identity/state machine. They are not
evidence of any real provider's response contract.
"""
import threading
from types import SimpleNamespace

import pytest

from Settings.registration_outcome import RegistrationOutcomeObserver, RegistrationVerdict
from nexora_accounts.local_profiles import LocalProfileError, LocalProfileStore


ORIGIN = "https://platform.example.test"


class TestContract:
    origin = ORIGIN

    def interpret(self, status_code, payload):
        if status_code != 200 or not isinstance(payload, dict):
            return RegistrationVerdict("unconfirmed", "synthetic-inconclusive")
        match payload.get("synthetic_phase"):
            case "registered":
                return RegistrationVerdict("created", "synthetic-verified", "synthetic-register-success")
            case "rejected":
                return RegistrationVerdict("rejected", "synthetic-rejection")
            case "challenge":
                return RegistrationVerdict("verification_required", "synthetic-challenge")
            case _:
                return RegistrationVerdict("unconfirmed", "synthetic-inconclusive")


class FakeResponse:
    def __init__(self, page, payload, url=ORIGIN + "/hall/api/member/register"):
        self.url = url
        self.request = SimpleNamespace(
            method="POST", frame=SimpleNamespace(page=page)
        )
        self.payload = payload
        self.status = 200

    def finished(self):
        return None

    def json(self):
        return self.payload


class FakePage:
    url = ORIGIN + "/register"

    def __init__(self):
        self.listeners = []
        self.context = self
        self.queue = []

    def on(self, name, callback):
        assert name == "response"
        self.listeners.append(callback)

    def remove_listener(self, name, callback):
        self.listeners.remove(callback)

    def wait_for_timeout(self, ms):
        if self.queue:
            response = self.queue.pop(0)
            for cb in list(self.listeners):
                cb(response)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha
    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    return LocalProfileStore(tmp_path / "Nexora" / "profiles.json")


def create(store, area, run_id):
    p = store.create(area, device_mode="android", start_url=ORIGIN + "/register")
    store.record_session(p["id"], "starting", run_id)
    return p


def observe(store, profile, run_id, payload, *, contract=None):
    page = FakePage()
    watch = RegistrationOutcomeObserver(
        page, lambda _: None, profile["id"], expected_origin=page.url,
        contract=contract,
    )
    watch.start()
    # Exactly after the browser's synthetic Registrar click; not 'created'.
    before = store.update_registration_result(
        profile["id"], profile["accountId"], run_id, "confirming",
        "Confirmando cadastro pelo site.", site_origin=ORIGIN
    )
    assert before["status"] == "confirming"
    page.queue.append(FakeResponse(page, payload))
    status, reason = watch.finish(
        threading.Event(), wait_seconds=.02, verification_wait_seconds=0
    )
    saved = store.update_registration_result(
        profile["id"], profile["accountId"], run_id, status, reason,
        confirmation_source=(
            watch.verdict.confirmation_source if status == "created" else None
        ),
        site_origin=ORIGIN,
    )
    assert not page.listeners
    return saved


def test_created_rejected_and_inconclusive_are_isolated_and_persistent(store):
    p79 = create(store, "mae", "run-79")
    p80 = create(store, "filha", "run-80")
    p81 = create(store, "mae", "run-81")

    a = observe(store, p79, "run-79", {"synthetic_phase":"registered"},
                contract=TestContract())
    assert a["status"] == "created"
    assert a["registrationConfirmedAt"]
    assert a["registrationConfirmationSource"] == "synthetic-register-success"
    # Creating account 79 did not silently touch account 80 or 81.
    assert store.get(p80["id"])["status"] == "pending"
    assert store.get(p81["id"])["status"] == "pending"

    b = observe(store, p80, "run-80", {"synthetic_phase":"rejected"},
                contract=TestContract())
    c = observe(store, p81, "run-81", {"success": True},
                contract=TestContract())
    assert b["status"] == "rejected"
    assert c["status"] == "unconfirmed"
    assert b["registrationConfirmedAt"] is None
    assert c["registrationConfirmedAt"] is None

    reopened = LocalProfileStore(store.path)
    assert [reopened.get(p["id"])["status"] for p in (p79, p80, p81)] == [
        "created", "rejected", "unconfirmed"
    ]
    for p in (p79, p80, p81):
        assert reopened.get(p["id"])["platformUserId"] is None


def test_http_200_and_generic_success_do_not_mark_created(store):
    p = create(store, "mae", "run-x")
    result = observe(store, p, "run-x", {"success": True})
    assert result["status"] == "unconfirmed"
    assert result["registrationConfirmedAt"] is None


def test_verified_challenge_remains_verification_required_not_created(store):
    p = create(store, "filha", "run-challenge")
    result = observe(store, p, "run-challenge",
                     {"synthetic_phase":"challenge"}, contract=TestContract())
    assert result["status"] == "verification_required"
    assert result["registrationConfirmedAt"] is None


def test_closed_session_replaces_stale_confirming_with_unconfirmed(store):
    p = create(store, "mae", "run-close")
    store.update_registration_result(
        p["id"], p["accountId"], "run-close", "confirming",
        site_origin=ORIGIN,
    )
    store.record_session(p["id"], "closed", "run-close")
    assert LocalProfileStore(store.path).get(p["id"])["status"] == "unconfirmed"


def test_foreign_origin_uuid_and_stale_run_cannot_claim_confirmation(store):
    p = create(store, "mae", "run-79")
    q = create(store, "filha", "run-80")
    with pytest.raises(LocalProfileError, match="Origem"):
        store.update_registration_result(
            p["id"], p["accountId"], "run-79", "confirming",
            site_origin="https://other.example.test",
        )
    with pytest.raises(LocalProfileError, match="conta original"):
        store.update_registration_result(
            p["id"], q["accountId"], "run-79", "confirming",
            site_origin=ORIGIN,
        )
    store.record_session(p["id"], "starting", "new-run-79")
    with pytest.raises(LocalProfileError, match="execução diferente"):
        store.update_registration_result(
            p["id"], p["accountId"], "run-79", "created",
            confirmation_source="synthetic-verified", site_origin=ORIGIN,
        )
    assert store.get(p["id"])["status"] == "pending"
    assert store.get(q["id"])["status"] == "pending"


def test_observer_does_not_accept_post_from_another_page_same_origin(store):
    p = create(store, "mae", "run-filter")
    page = FakePage()
    other = FakePage()
    watch = RegistrationOutcomeObserver(
        page, lambda _: None, p["id"], expected_origin=page.url,
        contract=TestContract(),
    )
    watch.start()
    page.queue.extend([
        FakeResponse(other, {"synthetic_phase": "registered"}),
        FakeResponse(page, {"success": True}),
    ])
    result, reason = watch.finish(threading.Event(), wait_seconds=.02)
    assert result == "unconfirmed"
    assert watch.verdict.platform_user_id is None
    assert not page.listeners
