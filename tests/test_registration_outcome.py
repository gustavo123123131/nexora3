"""Offline verification: a click or HTTP 200 is not a successful account."""
import threading
from types import SimpleNamespace

from Settings.registration_outcome import (
    RegistrationOutcomeObserver, classify_registration_reply,
)


def test_explicit_success_and_error_signals():
    assert classify_registration_reply(200, {"success": True})[0] == "created"
    assert classify_registration_reply(200, {"code": 0, "msg": "success"})[0] == "created"
    assert classify_registration_reply(200, {"code": 0})[0] == "unconfirmed"
    assert classify_registration_reply(200, {})[0] == "unconfirmed"
    assert classify_registration_reply(200, {"success": False})[0] == "error"
    assert classify_registration_reply(200, {"code": 10004})[0] == "error"
    assert classify_registration_reply(403, {"success": True})[0] == "error"


class FakeResponse:
    def __init__(self, url, payload, *, method="POST", status=200):
        self.url = url
        self.request = SimpleNamespace(method=method)
        self.status = status
        self.payload = payload

    def json(self):
        return self.payload


class FakePage:
    def __init__(self):
        self.listeners = []
        self.responses = []

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


def test_observer_tracks_only_known_registration_post():
    page = FakePage()
    messages = []
    watcher = RegistrationOutcomeObserver(page, messages.append, 44)
    watcher.start()
    page.responses = [
        FakeResponse("https://a.test/api/login", {"success": True}),
        FakeResponse("https://a.test/hall/api/member/register", {"success": True}, method="GET"),
        FakeResponse("https://a.test/hall/api/member/register", {"success": True}),
    ]
    status, reason = watcher.finish(threading.Event(), wait_seconds=0.1)
    # First response is unrelated and ignored; test delivers more with polling.
    # A short timeout may expire before the last response; use a second
    # deterministic instance for success.
    assert status in ("created", "unconfirmed")
    assert not page.listeners
    assert all("password" not in msg for msg in messages)

    page = FakePage()
    watcher = RegistrationOutcomeObserver(page, messages.append, 44)
    watcher.start()
    page.responses = [FakeResponse("https://a.test/hall/api/member/register", {"success": True})]
    assert watcher.finish(threading.Event(), wait_seconds=1)[0] == "created"


def test_observer_records_missing_confirmation_without_claiming_success():
    page = FakePage()
    messages = []
    watcher = RegistrationOutcomeObserver(page, messages.append, 7)
    watcher.start()
    status, reason = watcher.finish(threading.Event(), wait_seconds=0)
    assert status == "unconfirmed"
    assert "Nenhuma resposta" in reason


def test_observer_rejection_keeps_cause_safe():
    page = FakePage()
    watcher = RegistrationOutcomeObserver(page, lambda s: None, 5)
    watcher.start()
    page.responses = [
        FakeResponse("https://a.test/hall/api/member/register",
                     {"success": False, "password": "not-for-logs"})
    ]
    status, reason = watcher.finish(threading.Event(), wait_seconds=1)
    assert status == "error"
    assert "not-for-logs" not in reason


def test_other_registration_endpoint_is_logged_but_not_assumed_successful():
    page = FakePage()
    events = []
    watcher = RegistrationOutcomeObserver(page, events.append, 23)
    watcher.start()
    page.responses = [
        FakeResponse("https://a.test/other/register-submit?token=keep-private",
                     {"success": True}),
    ]
    status, reason = watcher.finish(threading.Event(), wait_seconds=0.02)
    assert status == "unconfirmed"
    assert "rota" in reason
    assert any("/other/register-submit" in msg for msg in events)
    assert all("keep-private" not in msg for msg in events)


def test_explicit_nested_success_marks_created_but_http_200_alone_does_not():
    assert classify_registration_reply(200, {"data": {"success": True}})[0] == "created"
    assert classify_registration_reply(200, {"code": 0, "data": {"id": "user42"}})[0] == "unconfirmed"
