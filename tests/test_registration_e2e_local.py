"""Full Abrir Conta registration flow in REAL Chromium against loopback demo.

No external API, synthetic identity cannot leave 127.0.0.1.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

from nexora_accounts.registration_autofill import autofill_existing_registration
from nexora_accounts.registration_submit import RegistrationWatcher, request_demo_submit
from nexora_accounts.service import AccountsRepository
from nexora_accounts.registration_trace import trace_registration


HTML = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Cadastro de demonstração</title></head>
<body>
<form id="demo-registration-form">
  <input name="account" data-input-name="account" type="text">
  <input name="userpass" maxlength="16" dir="ltr" autocomplete="new-password" data-input-name="userpass" type="text" placeholder="Insira a senha">
  <input name="confirmPassword" maxlength="16" dir="ltr" autocomplete="new-password" data-input-name="confirmPassword" type="text" placeholder="Confirmar senha">
  <input name="realName" data-input-name="realName" type="text" placeholder="Introduza o seu nome real">
  <input name="email" data-input-name="email" type="email">
  <button type="button" class="ui-button ui-button--primary ui-button--normal ui-button--block" id="insideRegisterSubmitClick"><span class="ui-button__text">Registro</span></button>
</form>
<div id="demo-registration-success" hidden>Cadastro concluído pelo servidor de teste</div>
<script>
document.querySelector("#insideRegisterSubmitClick").addEventListener("click", async () => {
  const body = Object.fromEntries(new FormData(document.querySelector("form")).entries());
  const response = await fetch("/hall/api/member/register", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  if (response.ok) document.querySelector("#demo-registration-success").hidden = false;
});
</script>
</body></html>"""


@contextmanager
def demo_registration_server():
    requests = {"posted": 0, "field_names": set(), "real_name_filled": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != "/register":
                self.send_error(404)
                return
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/hall/api/member/register":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            # Only store NAMES of fields, never their values or personal data.
            requests["posted"] += 1
            requests["field_names"] = set(data)
            requests["real_name_filled"] = bool(data.get("realName"))
            body = b'{"code":0}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/register", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_chromium_real_page_and_local_http_submit_and_status(tmp_path):
    with demo_registration_server() as (url, requests):
        repo = AccountsRepository(tmp_path / "accounts.json")
        plans = repo.create_launch_profiles(
            "mae", "desktop", [url], 1, tmp_path / "profiles")
        key = plans[0]["profile_record_id"]
        events = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded")
                result = autofill_existing_registration(page, plans[0], repo)
                assert result["status"] == "filled"
                assert repo.account(key)["status"] == "pending"
                assert page.locator("input[data-input-name='userpass']").input_value() == (
                    repo.account(key)["profileData"]["password"]
                )
                assert page.locator("input[data-input-name='confirmPassword']").input_value() == (
                    repo.account(key)["profileData"]["password"]
                )
                assert len(repo.account(key)["profileData"]["password"]) == 9
                assert page.locator("input[data-input-name='realName']").input_value() == (
                    repo.account(key)["profileData"]["fullName"]
                )
                watcher = RegistrationWatcher(
                    page, plans[0], repository=repo, log=events.append,
                    success_selector="#demo-registration-success"
                )
                assert request_demo_submit(key, repo) is True
                assert request_demo_submit(key, repo) is False
                watcher.tick()  # click is made IN Playwright owning thread
                page.locator("#demo-registration-success").wait_for(state="visible", timeout=5000)
                for _ in range(15):
                    watcher.tick()
                    if repo.account(key)["status"] == "completed":
                        break
                    page.wait_for_timeout(150)
                assert requests["posted"] == 1
                assert requests["real_name_filled"] is True
                assert {"account", "userpass", "confirmPassword", "realName"}.issubset(
                    requests["field_names"]
                )
                stored = AccountsRepository(repo.path).account(key)
                assert stored["status"] == "completed"
                assert stored["stage"] == "provider-confirmed"
                log_text = json.dumps(stored["registrationLog"])
                assert "password" not in log_text.lower()
                assert "userpass" not in log_text.lower()
                assert any("provider-confirmed" in entry["stage"] for entry in stored["registrationLog"])
                # Server-side record list survives a refresh/repository reinitialization.
                assert len(AccountsRepository(repo.path).accounts(profiles_only=True)) == 1
            finally:
                browser.close()


def test_accounts_ui_displays_only_three_fields_from_created_profiles(tmp_path):
    """Real repository Abrir Conta data -> actual DOM cards in Chromium."""
    from pathlib import Path

    repo = AccountsRepository(tmp_path / "accounts.json")
    repo.create_launch_profiles(
        "mae", "desktop", [], 1, tmp_path / "profiles"
    )
    repo.create_launch_profiles(
        "filha", "android", [], 1, tmp_path / "profiles_filha"
    )
    accounts = repo.accounts(profiles_only=True)
    assert len(accounts) == 2
    source = Path("webui/static/accounts_view.js").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<!doctype html><html><body><div id='app'></div></body></html>")
            page.add_script_tag(content=source)
            page.evaluate("""(accounts) => {
              window.__testAccounts = accounts;
              window.__directory = window.NexoraAccountsView.create({
                get: async () => ({accounts:window.__testAccounts}),
              });
              document.querySelector("#app").append(window.__directory.node);
              window.__directory.onShow();
            }""", accounts)
            page.locator(".accounts-card").first.wait_for(timeout=5000)
            assert page.locator(".accounts-card").count() == 2
            for record in accounts:
                card = page.locator(".accounts-card").filter(
                    has=page.locator("h3", has_text=record["displayName"])
                )
                assert card.count() == 1
                assert card.get_by_text(record["profileData"]["fullName"], exact=True).count() == 1
                assert card.get_by_text(record["profileData"]["username"], exact=True).count() == 1
                assert card.get_by_text(record["profileData"]["email"], exact=True).count() == 1
                assert card.locator(".accounts-summary-item").count() == 3
                assert card.locator(".accounts-summary-item span").all_text_contents() == [
                    "Nome da conta", "Username", "E-mail"
                ]
                # Credentials and other identity fields must not be in the DOM.
                assert record["profileData"]["password"] not in card.inner_text()
                assert record["profileData"]["cpf"] not in card.inner_text()
            assert page.get_by_role("button", name="Ver detalhes").count() == 0
            assert page.locator(".accounts-details").count() == 0
            assert page.locator(".accounts-registration-log").count() == 0
            assert page.get_by_text("Cadastro", exact=True).count() == 0
            assert page.get_by_text("Configurar site").count() == 0
            assert page.get_by_text("Adicionar conta").count() == 0
            # The original read-only automatic refresh must still work.
            new_email = "atualizado@example.test"
            page.evaluate("""(email) => {
              window.__testAccounts[0].profileData.email=email;
            }""", new_email)
            page.get_by_text(new_email, exact=True).wait_for(timeout=6000)
            assert page.locator(".accounts-card").count() == 2
            page.close()
        finally:
            browser.close()


def test_real_site_layout_preview_fills_only_nonidentity_credentials(tmp_path):
    """Mock HTTPS document locally: NO outbound site call or registration."""
    url = "https://registration.preview.example.org/register"
    repo = AccountsRepository(tmp_path / "accounts.json")
    plan = repo.create_launch_profiles(
        "mae", "desktop", [url], 1, tmp_path / "profiles"
    )[0]
    data = repo.account(plan["profile_record_id"])["profileData"]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route(
                "https://registration.preview.example.org/**",
                lambda route: route.fulfill(
                    status=200, content_type="text/html; charset=utf-8",
                    body=HTML
                ),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            key = plan["profile_record_id"]
            completed_events = []
            def progress(stage):
                completed_events.append(stage)
                trace_registration(key, plan["idx"], stage, repository=repo)
            result = autofill_existing_registration(page, plan, repo, progress=progress)
            assert result["status"] == "credentials-filled"
            assert result["fieldCount"] == 3
            assert page.locator("input[data-input-name='account']").input_value() == data["username"]
            assert page.locator("input[data-input-name='userpass']").input_value() == data["password"]
            assert page.locator("input[data-input-name='confirmPassword']").input_value() == data["password"]
            assert page.locator("input[data-input-name='realName']").input_value() == ""
            assert page.locator("#insideRegisterSubmitClick").is_visible()
            assert repo.account(key)["stage"] == "submit-not-sent"
            assert repo.account(key)["status"] == "local"
            assert completed_events == [
                "username-filled", "password-filled", "password-confirmed",
                "real-name-field-found", "submit-button-found",
                "name-entry-pending", "submit-not-sent",
            ]
            assert len(repo.account(key)["registrationLog"]) >= 8
            assert all(
                "password" not in str(e).lower() or e["stage"] in
                {"password-filled", "password-confirmed"} for e in
                repo.account(key)["registrationLog"]
            )
            context.close()
        finally:
            browser.close()



def test_configured_owner_name_autofills_in_real_chromium_without_sending(
        tmp_path, monkeypatch):
    """Real browser, intercepted HTTPS document: field fill only, no signup POST."""
    monkeypatch.setenv("NEXORA_REGISTRATION_OWNER_NAME", "Titular Autorizado")
    url = "https://registration.preview.example.org/register"
    repo = AccountsRepository(tmp_path / "accounts.json")
    plan = repo.create_launch_profiles(
        "filha", "desktop", [url], 1, tmp_path / "profiles"
    )[0]
    account_id = plan["profile_record_id"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            post_count = []
            def intercept(route):
                if route.request.method.upper() != "GET":
                    post_count.append(route.request.method)
                route.fulfill(status=200, content_type="text/html; charset=utf-8",
                              body=HTML)
            context.route("https://registration.preview.example.org/**", intercept)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            result = autofill_existing_registration(
                page, plan, repo,
                progress=lambda stage: trace_registration(
                    account_id, plan["idx"], stage, repository=repo
                ),
            )
            assert result["status"] == "owner-name-filled"
            assert result["fieldCount"] == 4
            assert page.locator("input[data-input-name='realName']").input_value() == "Titular Autorizado"
            assert page.locator("#insideRegisterSubmitClick").is_visible()
            assert post_count == []
            assert "Titular Autorizado" not in repo.path.read_text(encoding="utf-8")
            assert repo.account(account_id)["stage"] == "submit-not-sent"
            assert any(
                entry["stage"] == "real-name-filled"
                for entry in repo.account(account_id)["registrationLog"]
            )
            context.close()
        finally:
            browser.close()



def test_browser_manual_name_and_registro_click_are_logged_in_chromium(tmp_path):
    """A manual browser click is observed and the local-only response classified."""
    with demo_registration_server() as (url, requests):
        repo = AccountsRepository(tmp_path / "accounts.json")
        plan = repo.create_launch_profiles(
            "mae", "desktop", [url], 1, tmp_path / "profiles"
        )[0]
        key = plan["profile_record_id"]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded")
                result = autofill_existing_registration(page, plan, repo)
                assert result["status"] == "filled"
                watcher = RegistrationWatcher(
                    page, plan, repository=repo,
                    success_selector="#demo-registration-success"
                )
                # Simulate a person editing the realName input in the page.
                page.locator("input[data-input-name='realName']").fill("Titular do Teste")
                watcher.tick()
                assert any(
                    e["stage"] == "name-entered-in-browser"
                    for e in repo.account(key)["registrationLog"]
                )
                page.locator("#insideRegisterSubmitClick").click()
                watcher.tick()
                assert any(
                    e["stage"] == "manual-submit-clicked"
                    for e in repo.account(key)["registrationLog"]
                )
                page.locator("#demo-registration-success").wait_for(
                    state="visible", timeout=5000
                )
                for _ in range(16):
                    watcher.tick()
                    if repo.account(key)["status"] == "completed":
                        break
                    page.wait_for_timeout(120)
                assert requests["posted"] == 1
                assert requests["real_name_filled"]
                assert repo.account(key)["status"] == "completed"
                assert repo.account(key)["stage"] == "provider-confirmed"
                logs = json.dumps(repo.account(key)["registrationLog"])
                assert "Titular do Teste" not in logs
                assert repo.account(key)["profileData"]["password"] not in logs
            finally:
                browser.close()



def test_form_name_fills_when_profile_owner_is_confirmed_in_mock_https(tmp_path):
    """Regression: selector works; identity policy is what blocks synthetic data.

    This is a mocked HTTPS document in local Chromium, not a real provider.
    """
    url = "https://registration.preview.example.org/register"
    repo = AccountsRepository(tmp_path / "accounts.json")
    plan = repo.create_launch_profiles(
        "mae", "desktop", [url], 1, tmp_path / "profiles"
    )[0]
    key = plan["profile_record_id"]
    state = json.loads(repo.path.read_text(encoding="utf-8"))
    profile = next(row["profileData"] for row in state["accounts"]
                   if row["id"] == key)
    profile["isTestData"] = False
    profile["ownerConfirmed"] = True
    profile["fullName"] = "Titular Confirmado"
    repo.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    network_posts = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            def serve_mock(route):
                if route.request.method.upper() != "GET":
                    network_posts.append(route.request.method)
                route.fulfill(status=200, content_type="text/html; charset=utf-8",
                              body=HTML)
            context.route("https://registration.preview.example.org/**", serve_mock)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            result = autofill_existing_registration(page, plan, repo)
            assert result["status"] == "filled"
            assert page.locator('input[data-input-name="realName"]').input_value() == "Titular Confirmado"
            assert page.locator('input[data-input-name="account"]').input_value() == profile["username"]
            assert page.locator('input[data-input-name="userpass"]').input_value() == profile["password"]
            assert network_posts == []
            assert repo.account(key)["stage"] == "review"
            context.close()
        finally:
            browser.close()
