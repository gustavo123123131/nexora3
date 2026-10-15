"""Offline tests: never submit to external servers."""
import uuid

import pytest

from nexora_accounts.registration_submit import (
    RegistrationWatcher, request_submit, request_confirmed_auto_submit,
    request_autosubmit_after_fill
)


class Repo:
    def __init__(self, account):
        self.account_record = dict(account)
        self.events = []

    def account(self, key):
        assert key == self.account_record["id"]
        return dict(self.account_record)

    def set_stage(self, key, status, stage, http_status=None):
        assert key == self.account_record["id"]
        self.account_record.update(status=status, stage=stage)
        self.events.append({"status": status, "stage": stage, "http": http_status})


class Locator:
    def __init__(self, present=True):
        self.first = self
        self.present = present
        self.clicks = 0
        self.fills = []

    def is_visible(self):
        return self.present

    def wait_for(self, state="visible", timeout=None):
        if state != "visible" or not self.present:
            raise TimeoutError("Botão de registro indisponível")

    def click(self, timeout=None):
        self.clicks += 1

    def fill(self, value, timeout=None):
        self.fills.append(value)

    def get_attribute(self, _name):
        return "text"


class Page:
    def __init__(self, url):
        self.url = url
        self.closed = False
        self.callbacks = {}
        self.button = Locator()
        self.success = Locator(False)
        self.fields = {f"input[data-input-name='{name}']": Locator()
                       for name in ("account", "userpass", "confirmPassword",
                                    "realName", "email", "phone")}

    def on(self, event, callback):
        self.callbacks[event] = callback

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def is_closed(self):
        return self.closed

    def locator(self, selector):
        if selector == "#insideRegisterSubmitClick":
            return self.button
        if selector == "#registration-success":
            return self.success
        return self.fields.get(selector, Locator(False))

    def response(self, status):
        class Request:
            method = "POST"

        class Response:
            url = "https://api.example.org/hall/api/member/register"
            request = Request()

            def __init__(self, status):
                self.status = status

        self.callbacks["response"](Response(status))


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


def valid_account():
    return {
        "id": str(uuid.uuid4()),
        "targetUrl": "https://example.org/register",
        "profileData": {
            "isTestData": False,
            "ownerConfirmed": True,
            "password": "SEGREDO-QUE-NAO-DEVE-APARECER-EM-LOG",
        },
        "stage": "review",
        "status": "pending",
    }


def test_request_needs_confirmation_and_blocks_test_identities():
    account = valid_account()
    repo = Repo(account)
    with pytest.raises(Exception):
        request_submit(account["id"], False, repo)
    repo.account_record["profileData"]["isTestData"] = True
    with pytest.raises(Exception):
        request_submit(account["id"], True, repo)
    assert not repo.events


def test_autoclick_after_autofill_only_for_confirmed_owner():
    account = valid_account()
    account["profileData"].update({
        "fullName": "Pessoa Autorizada",
        "email": "pessoa@dominio-valido.dev",
        "phone": "",
    })
    repo = Repo(account)
    watcher = RegistrationWatcher(
        Page(account["targetUrl"]), {"profile_record_id": account["id"]},
        repository=repo, success_selector="",
    )
    assert request_confirmed_auto_submit(account["id"], repo) is True
    watcher.tick()
    assert watcher.page.button.clicks == 1
    watcher.tick()
    assert watcher.page.button.clicks == 1

    account["profileData"]["isTestData"] = True
    assert request_confirmed_auto_submit(account["id"], repo) is False


def test_click_sent_once_and_http_200_not_mistaken_for_success():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key, "idx": 2},
                                  repository=repo, clock=clock,
                                  success_selector="#registration-success")
    assert request_submit(key, True, repo)["queued"]
    assert request_submit(key, True, repo)["duplicate"]
    watcher.tick()
    assert page.button.clicks == 1
    assert repo.account_record["status"] == "pending"
    page.response(200)
    clock.value = 101
    watcher.tick()
    assert repo.account_record["status"] == "pending"
    assert repo.account_record["stage"] == "provider-response-unverified"
    assert repo.events[-1]["http"] == 200
    assert all("SEGREDO" not in str(event) for event in repo.events)
    clock.value = 102
    page.success.present = True
    watcher.tick()
    assert repo.account_record["status"] == "completed"
    assert repo.account_record["stage"] == "provider-confirmed"
    assert page.button.clicks == 1


def test_http_error_is_pending_until_provider_evidence():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, clock=clock,
                                  success_selector="#registration-success")
    request_submit(key, True, repo)
    watcher.tick()
    page.response(422)
    clock.value = 102
    watcher.tick()
    assert repo.account_record["status"] == "pending"
    assert repo.account_record["stage"] == "provider-http-error"


def test_unexpected_origin_blocks_submission():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page = Page("https://wrong.example.org/register")
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, success_selector="")
    request_submit(key, True, repo)
    watcher.tick()
    assert page.button.clicks == 0
    assert repo.account_record["stage"] == "origin-changed"


def test_timeout_keeps_account_pending_and_does_not_retry():
    repo = Repo(valid_account())
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, clock=clock, success_selector="")
    request_submit(key, True, repo)
    watcher.tick()
    clock.value = 150
    watcher.tick()
    assert repo.account_record["status"] == "pending"
    assert repo.account_record["stage"] == "timeout-unverified"
    clock.value = 152
    watcher.tick()
    assert page.button.clicks == 1


def test_json_events_are_bounded_and_exclude_credentials(tmp_path):
    from nexora_accounts.service import AccountsRepository
    from Settings.device_modes import profile_directory

    repo = AccountsRepository(tmp_path / "accounts.json")
    plan = repo.create_launch_profiles(
        "mae", "desktop", ["https://example.org/register"], 1, tmp_path / "profiles"
    )[0]
    key = plan["profile_record_id"]
    for _ in range(40):
        repo.set_stage(key, "pending", "provider-response-unverified", http_status=200)
    row = repo.account(key)
    assert len(row["registrationLog"]) == 30
    assert all(event == {"at": event["at"], "stage": "provider-response-unverified",
                         "httpStatus": 200} for event in row["registrationLog"])



def test_real_values_are_transient_and_submit_using_existing_page():
    repo = Repo(valid_account())
    repo.account_record["profileData"] = {"isTestData": True}
    repo.account_record["stage"] = "test-data-blocked"
    repo.account_record["status"] = "local"
    key = repo.account_record["id"]
    page, clock = Page("https://example.org/register"), Clock()
    watcher = RegistrationWatcher(page, {"profile_record_id": key},
                                  repository=repo, clock=clock,
                                  success_selector="#registration-success")
    values = {
        "username": "titular_123",
        "fullName": "Titular Autorizado",
        "email": "titular@provedor.example",
        "phone": "+55 11987654321",
        "password": "SenhaSegura!123",
        "confirmPassword": "SenhaSegura!123",
    }
    assert request_submit(key, True, repo, values)["queued"]
    watcher.tick()
    assert page.fields["input[data-input-name='account']"].fills == ["titular_123"]
    assert page.fields["input[data-input-name='userpass']"].fills == ["SenhaSegura!123"]
    assert page.button.clicks == 1
    assert repo.account_record["profileData"] == {"isTestData": True}
    assert all("SenhaSegura!123" not in str(event) for event in repo.events)


def test_rejects_invalid_transient_values():
    repo = Repo(valid_account())
    values = {"username": "abc", "fullName": "Titular Autorizado",
              "email": "falso@example.test", "password": "SenhaSegura!123",
              "confirmPassword": "SenhaSegura!123"}
    with pytest.raises(Exception):
        request_submit(repo.account_record["id"], True, repo, values)
    assert not repo.events


def test_real_chromium_clicks_supplied_registro_button_without_external_network():
    """Real Playwright DOM click, mocked HTTPS navigation, no provider requests."""
    from playwright.sync_api import sync_playwright
    account = valid_account()
    account["profileData"].update({
        "fullName": "Pessoa Autorizada",
        "email": "pessoa@dominio-valido.dev",
        "phone": "",
    })
    repo = Repo(account)
    log = []
    html = """<!DOCTYPE html><html><body>
      <input data-input-name="account">
      <input data-input-name="userpass" type="password">
      <input data-input-name="confirmPassword" type="password">
      <input data-input-name="realName">
      <button type="button" id="insideRegisterSubmitClick">Registro</button>
      <script>
      window.nexoraClickCount = 0;
      document.getElementById('insideRegisterSubmitClick').addEventListener(
        'click', () => { window.nexoraClickCount++; });
      </script>
      </body></html>"""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("https://example.org/**", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(account["targetUrl"])
            for field, value in (
                ("account", "titular123"),
                ("userpass", "SenhaSegura!123"),
                ("confirmPassword", "SenhaSegura!123"),
                ("realName", "Pessoa Autorizada"),
            ):
                page.locator(f"input[data-input-name='{field}']").fill(value)
            watcher = RegistrationWatcher(
                page, {"profile_record_id": account["id"], "idx": 1},
                repository=repo, log=log.append, success_selector="")
            assert request_autosubmit_after_fill(
                page, {"profile_record_id": account["id"]},
                {"status": "filled"}, log.append, repository=repo) is True
            watcher.tick()
            assert page.evaluate("window.nexoraClickCount") == 1
            watcher.tick()
            assert page.evaluate("window.nexoraClickCount") == 1
            assert any("Campo realName: preenchido" in entry for entry in log)
            assert any("CLIQUE: iniciando click()" in entry for entry in log)
            assert any("CLIQUE EXECUTADO:" in entry for entry in log)
            assert repo.account_record["status"] == "pending"
            assert not any("SenhaSegura!123" in entry for entry in log)
        finally:
            browser.close()


def test_diagnostics_explain_why_generated_external_profile_is_not_sent():
    account = valid_account()
    account["profileData"]["isTestData"] = True
    repo = Repo(account)
    page = Page(account["targetUrl"])
    messages = []
    assert request_autosubmit_after_fill(
        page, {"profile_record_id": account["id"]},
        {"status": "profile-name-filled"}, messages.append,
        repository=repo) is False
    assert any("CLIQUE NÃO SOLICITADO" in line for line in messages)
    assert not any("CLIQUE EXECUTADO" in line for line in messages)
    assert page.button.clicks == 0


def test_owned_test_origin_requires_exact_https_opt_in(monkeypatch):
    from nexora_accounts.registration_autofill import _owned_test_origin
    monkeypatch.delenv("NEXORA_OWNED_TEST_ORIGINS", raising=False)
    assert not _owned_test_origin("https://qa.example.org/register")
    monkeypatch.setenv("NEXORA_OWNED_TEST_ORIGINS", "https://qa.example.org")
    assert _owned_test_origin("https://qa.example.org/register")
    assert not _owned_test_origin("https://other.qa.example.org/register")
    assert not _owned_test_origin("https://qa.example.org:8443/register")
    assert not _owned_test_origin("http://qa.example.org/register")


def test_owned_https_site_full_prefill_then_one_registro_click(monkeypatch):
    """Real Chromium DOM: test identities remain isolated to explicit owned origin."""
    from playwright.sync_api import sync_playwright
    from nexora_accounts.registration_autofill import autofill_existing_registration

    monkeypatch.setenv("NEXORA_OWNED_TEST_ORIGINS", "https://qa.example.org")
    account = valid_account()
    account["targetUrl"] = "https://qa.example.org/register"
    account["profileData"] = {
        "isTestData": True,
        "username": "exemploqa",
        "password": "Senha@123",
        "fullName": "Pessoa de Teste",
        "email": "exemploqa@example.test",
        "phone": "+55 (11) 90000-1234",
        "cpfVerified": False,
    }
    repo = Repo(account)
    log = []
    html = """<!DOCTYPE html><html><body>
      <input data-input-name="account">
      <input data-input-name="userpass" type="password">
      <input data-input-name="confirmPassword" type="password">
      <input data-input-name="realName">
      <button type="button" id="insideRegisterSubmitClick">Registro</button>
      <script>
      window.clickCount = 0;
      document.querySelector("#insideRegisterSubmitClick").addEventListener(
        "click", () => { window.clickCount++; });
      </script>
      </body></html>"""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("https://qa.example.org/**", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(account["targetUrl"])
            plan = {"profile_record_id": account["id"], "idx": 1}
            result = autofill_existing_registration(page, plan, repo)
            assert result["status"] == "filled"
            assert page.locator("input[data-input-name='realName']").input_value() == "Pessoa de Teste"
            watcher = RegistrationWatcher(
                page, plan, repository=repo, log=log.append, success_selector=""
            )
            assert request_autosubmit_after_fill(
                page, plan, result, log.append, repository=repo
            ) is True
            watcher.tick()
            assert page.evaluate("window.clickCount") == 1
            watcher.tick()
            assert page.evaluate("window.clickCount") == 1
            assert any("CLIQUE EXECUTADO:" in line for line in log)
            assert repo.account_record["stage"] == "awaiting-provider"
            assert not any("Senha@123" in line for line in log)
        finally:
            browser.close()


def test_local_complete_profile_name_state_executes_registro_once():
    """The formerly rejected profile-name-filled state must click on a demo only."""
    from playwright.sync_api import sync_playwright

    account = valid_account()
    account["targetUrl"] = "http://127.0.0.1:19077/register"
    account["profileData"]["isTestData"] = True
    account["stage"] = "profile-name-prefilled"
    repo = Repo(account)
    logs = []
    html = """<!DOCTYPE html><html><body>
      <input data-input-name="account" value="teste123">
      <input data-input-name="userpass" type="password" value="Senha123!">
      <input data-input-name="confirmPassword" type="password" value="Senha123!">
      <input data-input-name="realName" value="Pessoa de Teste">
      <button type="button" id="insideRegisterSubmitClick">Registro</button>
      <script>
        window.registroClicks = 0;
        document.getElementById("insideRegisterSubmitClick").addEventListener(
          "click", () => { window.registroClicks++; });
      </script>
    </body></html>"""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("http://127.0.0.1:19077/**", lambda route: route.fulfill(
                status=200, content_type="text/html", body=html))
            page.goto(account["targetUrl"])
            plan = {"profile_record_id": account["id"], "idx": 1}
            watcher = RegistrationWatcher(page, plan, repository=repo,
                                          log=logs.append, success_selector="")
            assert request_autosubmit_after_fill(
                page, plan, {"status": "profile-name-filled", "fieldCount": 4},
                logs.append, repository=repo) is True
            watcher.tick()
            assert page.evaluate("window.registroClicks") == 1
            watcher.tick()
            assert page.evaluate("window.registroClicks") == 1
            assert any("CLIQUE EXECUTADO" in entry for entry in logs)
        finally:
            browser.close()
