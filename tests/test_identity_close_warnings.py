"""Offline regressions for Playwright browser shutdown and JS regex warnings.

These tests do not contact the platform or authenticate an account.
"""
from pathlib import Path
import warnings

from Settings.platform_identity import PlatformIdentityObserver
from test_x_object_id import (
    PAGE, Page, Response, ReviewedSyntheticFiveGBet,
    auth_body, make, send, start,
)
from test_platform_identity import (
    Page as ResponsePage, FakeResponse, Contract,
    account as synthetic_account, new as synthetic_observer,
)


def test_platform_identity_compiles_with_escape_warnings_as_errors():
    path = Path(__file__).resolve().parents[1] / "Settings/platform_identity.py"
    source = path.read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        warnings.simplefilter("error", SyntaxWarning)
        compile(source, str(path), "exec")
    assert 'r"""() => {' in source


def test_storage_scanner_keeps_javascript_regex_backslashes():
    class ScriptPage(Page):
        url = PAGE
        captured = None

        def evaluate(self, script):
            self.captured = script
            return []

    page = ScriptPage()
    watcher = PlatformIdentityObserver(
        page, lambda *_: None,
        {"id": 79, "accountId": "account-79", "runId": "run-79"},
        expected_origin=PAGE,
    )
    watcher.start()
    try:
        assert watcher.observe_candidate() is None
        assert r"raw.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\." in page.captured
    finally:
        watcher.close()


def test_builtin_header_checker_does_not_spawn_response_finished(monkeypatch, tmp_path):
    from nexora_accounts.local_profiles import LocalProfileStore
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha

    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    store = LocalProfileStore(tmp_path / "profiles.json")
    profile = make(store, "mae", "run-closed")
    page = Page()
    calls = []

    def forbidden_finished(self):
        calls.append(self)
        raise RuntimeError("simulated target closed")

    monkeypatch.setattr(Response, "finished", forbidden_finished)
    watcher = start(page, profile, "run-closed")
    try:
        send(page, 951357, body={"err_code": 41000})
        assert watcher.observe_candidate().user_id == "951357"
        watcher.after_registration_click()
        assert watcher.identify(wait_seconds=0) is None
        assert not calls  # Never schedule Playwright's on_finished coroutine.
    finally:
        watcher.close()


def test_synthetic_validated_header_still_works_without_finished(monkeypatch, tmp_path):
    from nexora_accounts.local_profiles import LocalProfileStore
    from Settings.Criar_Mae.settings import utils as mae
    from Settings.Criar_Filha.settings import utils as filha

    monkeypatch.setattr(mae, "PROFILES_DIR", tmp_path / "mae")
    monkeypatch.setattr(filha, "PROFILES_DIR", tmp_path / "filha")
    store = LocalProfileStore(tmp_path / "profiles.json")
    profile = make(store, "mae", "run-a")
    adapter = ReviewedSyntheticFiveGBet({
        profile["accountId"]: (profile["id"], "run-a", "991357"),
    })
    calls = []

    def forbidden_finished(self):
        calls.append(self)
        raise RuntimeError("simulated target closed")

    monkeypatch.setattr(Response, "finished", forbidden_finished)
    page = Page()
    watcher = start(page, profile, "run-a", adapter)
    try:
        watcher.after_registration_click()
        send(page, 991357, body=auth_body(profile, "run-a", "991357"))
        identity = watcher.identify(wait_seconds=0)
        assert identity is not None and identity.user_id == "991357"
        assert not calls
    finally:
        watcher.close()


def test_response_contract_does_not_call_finished(monkeypatch):
    page = ResponsePage()
    watcher = synthetic_observer(
        page, synthetic_account(79, "user79", "run-79"), Contract(),
    )
    response = FakeResponse(
        "https://platform.example.test/api/me",
        {"remote_member_number": "000079"},
    )
    calls = []

    def forbidden_finished():
        calls.append(True)
        raise RuntimeError("simulated target closed")

    response.finished = forbidden_finished
    try:
        page.emit(response)
        identity = watcher.identify(wait_seconds=0)
        assert identity is not None and identity.user_id == "000079"
        assert not calls
    finally:
        watcher.close()


def test_closing_browser_skips_pending_response_inspection():
    class ClosingPage(Page):
        closed = False

        def is_closed(self):
            return self.closed

    page = ClosingPage()
    watcher = PlatformIdentityObserver(
        page, lambda *_: None,
        {"id": 79, "accountId": "account-79", "runId": "run-79"},
        expected_origin=PAGE,
    )
    watcher.start()
    req = send(page, 567890, body={"err_code": 0})
    page.closed = True
    try:
        watcher.inspect_pending_headers()
        assert req.calls == 0
        assert watcher.observe_candidate() is None
        assert watcher.identify(wait_seconds=0) is None
    finally:
        watcher.close()
    assert not page.listeners["request"] and not page.listeners["response"]
