"""Offline registration state-machine tests. Synthetic contracts are NOT proof of production."""
import threading
from types import SimpleNamespace

from Settings.registration_outcome import (
    RegistrationOutcomeObserver, RegistrationVerdict,
    classify_registration_reply,
)


class SyntheticReviewedContract:
    """ONLY A TEST DOUBLE. Production has no verified provider adapter."""
    origin = "https://platform.example.test"

    def interpret(self, http_status, payload):
        if http_status != 200 or not isinstance(payload, dict):
            return RegistrationVerdict("unconfirmed", "unverified")
        if payload.get("test_phase") == "challenge":
            return RegistrationVerdict("verification_required", "verify")
        if payload.get("test_phase") == "success":
            return RegistrationVerdict(
                "created", "verified",
                "synthetic-test-registration-response",
                payload.get("remote_id"),
                "synthetic-test-member-id" if payload.get("remote_id") else None,
            )
        if payload.get("test_phase") == "refused":
            return RegistrationVerdict("rejected", "refused")
        return RegistrationVerdict("unconfirmed", "unverified")


class FakeResponse:
    def __init__(self, url, payload, *, method="POST", status=200, finished_error=None):
        self.url = url
        self.request = SimpleNamespace(method=method)
        self.status = status
        self.payload = payload
        self.finished_error = finished_error

    def finished(self):
        return self.finished_error

    def json(self):
        return self.payload


class FakePage:
    url = "https://platform.example.test/register"

    def __init__(self):
        self.listeners = []
        self.responses = []
        self.context = self

    def on(self, event, callback):
        assert event == "response"
        self.listeners.append(callback)

    def remove_listener(self, event, callback):
        self.listeners.remove(callback)

    def wait_for_timeout(self, ms):
        if self.responses:
            response = self.responses.pop(0)
            for cb in list(self.listeners):
                cb(response)


def watch(page=None, *, contract=None):
    page = page or FakePage()
    messages = []
    observer = RegistrationOutcomeObserver(
        page, messages.append, 44,
        expected_origin=page.url,
        contract=contract,
    )
    observer.start()
    return page, observer, messages


def test_generic_signals_do_not_certify_without_production_contract():
    for payload in (
        {"success": True}, {"data": {"success": True}},
        {"code": 0, "msg": "success"}, {"status": "created"},
        {"code": 1126}, {"success": False}, {"code": 10004}, {},
    ):
        assert classify_registration_reply(200, payload)[0] == "unconfirmed"
    assert classify_registration_reply(403, {"success": True})[0] == "unconfirmed"
    assert classify_registration_reply(200, {"test_phase": "success"},
                                       origin="https://wrong.test",
                                       contract=SyntheticReviewedContract())[0] == "unconfirmed"


def test_observer_waits_for_later_response_after_verification():
    page, observer, messages = watch(contract=SyntheticReviewedContract())
    path = "/hall/api/member/register"
    page.responses = [
        FakeResponse("https://platform.example.test/api/login", {"test_phase": "success"}),
        FakeResponse("https://other.test" + path, {"test_phase": "success"}),
        FakeResponse("https://platform.example.test" + path, {"test_phase": "challenge"}),
        FakeResponse("https://platform.example.test" + path,
                     {"test_phase": "success", "remote_id": "000042"}),
    ]
    status, _ = observer.finish(threading.Event(), wait_seconds=.1,
                                verification_wait_seconds=.1)
    assert status == "created"
    assert observer.verdict.platform_user_id == "000042"
    assert observer.verdict.platform_id_source == "synthetic-test-member-id"
    assert len(observer.responses) == 2   # page/context duplicates deduplicated
    assert not page.listeners
    assert any("verificação" not in s or "verification_required" in s for s in messages)
    assert "000042" not in "\n".join(messages)


def test_unfinished_response_cannot_confirm_and_does_not_log_payload():
    page, observer, messages = watch(contract=SyntheticReviewedContract())
    page.responses = [FakeResponse(
        "https://platform.example.test/hall/api/member/register",
        {"test_phase": "success", "remote_id": "secret-ID"},
        finished_error="request failed",
    )]
    assert observer.finish(threading.Event(), wait_seconds=.02)[0] == "unconfirmed"
    assert "secret-ID" not in "\n".join(messages)


def test_challenge_without_final_response_remains_pending_verification():
    page, observer, messages = watch(contract=SyntheticReviewedContract())
    page.responses = [FakeResponse(
        "https://platform.example.test/hall/api/member/register",
        {"test_phase": "challenge"},
    )]
    assert observer.finish(threading.Event(), wait_seconds=.01,
                           verification_wait_seconds=0)[0] == "verification_required"


def test_rejection_requires_proven_per_origin_contract():
    page, observer, _ = watch(contract=SyntheticReviewedContract())
    page.responses = [FakeResponse(
        "https://platform.example.test/hall/api/member/register",
        {"test_phase": "refused"},
    )]
    assert observer.finish(threading.Event(), wait_seconds=.1)[0] == "rejected"

    page, observer, _ = watch()
    page.responses = [FakeResponse(
        "https://platform.example.test/hall/api/member/register",
        {"test_phase": "refused"},
    )]
    assert observer.finish(threading.Event(), wait_seconds=.01)[0] == "unconfirmed"


def test_other_registration_endpoint_logged_without_secrets():
    page, observer, events = watch()
    page.responses = [FakeResponse(
        "https://platform.example.test/other/register-submit?token=keep-private",
        {"success": True},
    )]
    status, reason = observer.finish(threading.Event(), wait_seconds=.02)
    assert status == "unconfirmed"
    assert "rota" in reason
    assert any("/other/register-submit" in msg for msg in events)
    assert all("keep-private" not in msg for msg in events)


def test_origin_not_detectable_disables_attribution():
    page = FakePage()
    observer = RegistrationOutcomeObserver(page, lambda _: None, 79, expected_origin="about:blank")
    observer.start()
    page.responses = [FakeResponse(
        "https://platform.example.test/hall/api/member/register",
        {"test_phase": "success", "remote_id": "10"},
    )]
    assert observer.finish(threading.Event(), wait_seconds=.01)[0] == "unconfirmed"
    assert observer.verdict.platform_user_id is None
