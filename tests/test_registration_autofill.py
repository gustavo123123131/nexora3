"""Tests use in-memory Playwright stubs; no external site is contacted."""
from __future__ import annotations

import re
import threading

from Settings.registration_autofill import FIELDS, SUBMIT, fill_registration_form
from nexora_accounts.local_profiles import LocalProfileStore, LocalProfileError


class FakeLocator:
    def __init__(self, selector, events, broken=False, enabled=True, click_error=None):
        self.selector = selector
        self.events = events
        self.value = ""
        self.broken = broken
        self.enabled = enabled
        self.click_error = click_error

    def wait_for(self, *, state, timeout):
        assert state == "visible"
        self.events.append(("wait", self.selector))

    def fill(self, value, *, timeout):
        self.value = value
        self.events.append(("fill", self.selector))

    def input_value(self, *, timeout):
        self.events.append(("read", self.selector))
        return "unexpected" if self.broken else self.value

    def count(self):
        return 1

    def is_enabled(self):
        return self.enabled

    def evaluate(self, script):
        assert "section.login-register-fragment-submit" in script
        assert "getAnimations" in script
        self.events.append(("pause-animations", self.selector))
        return {"sectionFound": True, "paused": 2}

    def click(self, *, timeout):
        if self.click_error:
            raise TimeoutError(self.click_error)
        self.events.append(("click", self.selector))


class FakePage:
    def __init__(self, broken=None):
        self.events = []
        self.covered = False
        self.locators = {
            selector: FakeLocator(selector, self.events, broken == selector)
            for _, selector, _ in FIELDS
        }
        self.locators[SUBMIT] = FakeLocator(SUBMIT, self.events)

    def locator(self, selector):
        assert selector in self.locators, f"Unexpected selector: {selector}"
        return self.locators[selector]

    def evaluate(self, _script, selector):
        assert selector == SUBMIT
        return {
            "missing": False, "inside": True, "covered": self.covered,
            "marker": "div.modal-overlay" if self.covered else "button#insideRegisterSubmitClick",
            "width": 250, "height": 50, "cssPointerEvents": "auto",
        }


def sample():
    return {
        "id": 42,
        "username": "josil0r82a",
        "password": "piXez#v3q",
        "confirmPassword": "piXez#v3q",
        "fullName": "Joana Silva",
    }


def test_fills_exact_fields_in_order_then_clicks(monkeypatch):
    monkeypatch.setattr("Settings.registration_autofill.time.sleep", lambda seconds: None)
    page = FakePage()
    messages = []
    assert fill_registration_form(page, sample(), messages.append, threading.Event())
    assert [event for event in page.events if event[0] == "fill"] == [
        ("fill", selector) for _, selector, _ in FIELDS
    ]
    assert page.events[-1] == ("click", SUBMIT)
    assert page.events[-2] == ("pause-animations", SUBMIT)
    assert any("animações pausadas=2" in msg for msg in messages)
    for _, selector, _ in FIELDS:
        assert ("read", selector) in page.events
    assert "Clique em Registrar enviado" in messages[-1]
    assert "piXez#v3q" not in "\n".join(messages)


def test_logs_disabled_button_failure(monkeypatch):
    monkeypatch.setattr("Settings.registration_autofill.time.sleep", lambda seconds: None)
    page = FakePage()
    page.locators[SUBMIT].enabled = False
    page.locators[SUBMIT].click_error = "element is not enabled"
    messages = []
    assert not fill_registration_form(page, sample(), messages.append, threading.Event())
    assert any("habilitado=não" in msg for msg in messages)
    assert any("manteve o botão desabilitado" in msg for msg in messages)
    assert ("click", SUBMIT) not in page.events


def test_logs_click_interception(monkeypatch):
    monkeypatch.setattr("Settings.registration_autofill.time.sleep", lambda seconds: None)
    page = FakePage()
    page.covered = True
    page.locators[SUBMIT].click_error = "another element intercepts pointer events"
    messages = []
    assert not fill_registration_form(page, sample(), messages.append, threading.Event())
    assert any("Iniciando clique" in msg for msg in messages)
    assert any("interceptando o clique" in msg for msg in messages)
    assert any("sobreposição no botão" in msg and "div.modal-overlay" in msg for msg in messages)
    assert ("click", SUBMIT) not in page.events


def test_aborts_without_click_if_field_value_mismatch(monkeypatch):
    monkeypatch.setattr("Settings.registration_autofill.time.sleep", lambda seconds: None)
    page = FakePage(broken=FIELDS[1][1])
    messages = []
    assert not fill_registration_form(page, sample(), messages.append, threading.Event())
    assert ("click", SUBMIT) not in page.events
    assert "Senha" in messages[-1]


def test_stop_aborts_before_filling(monkeypatch):
    monkeypatch.setattr("Settings.registration_autofill.time.sleep", lambda seconds: None)
    page = FakePage()
    stopped = threading.Event()
    stopped.set()
    assert not fill_registration_form(page, sample(), lambda message: None, stopped)
    assert not page.events


def test_select_existing_profile_and_clear(tmp_path):
    store = LocalProfileStore(tmp_path / "generated_profiles.json")
    a = store.create("mae")
    b = store.create("filha")
    assert len(a["username"]) == 10
    assert len(b["username"]) == 10
    assert re.fullmatch(r"[a-z0-9]{10}", a["username"])
    assert store.selected() is None
    store.select(a["id"])
    assert store.selected()["id"] == a["id"]
    assert LocalProfileStore(store.path).selected_id() == a["id"]
    store.select(b["id"])
    assert store.selected()["id"] == b["id"]
    store.select(None)
    assert store.selected() is None
    try:
        store.select(99999999)
    except LocalProfileError:
        pass
    else:
        raise AssertionError("Unknown profiles should not be selected")
