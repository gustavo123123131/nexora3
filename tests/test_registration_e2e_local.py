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


HTML = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><title>Cadastro de demonstração</title></head>
<body>
<form id="demo-registration-form">
  <input name="account" data-input-name="account" type="text">
  <input name="userpass" data-input-name="userpass" type="password">
  <input name="confirmPassword" data-input-name="confirmPassword" type="password">
  <input name="realName" data-input-name="realName" type="text">
  <input name="email" data-input-name="email" type="email">
  <button type="submit" id="insideRegisterSubmitClick">Registro</button>
</form>
<div id="demo-registration-success" hidden>Cadastro concluído pelo servidor de teste</div>
<script>
document.querySelector("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget).entries());
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
    requests = {"posted": 0, "field_names": set()}

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


def test_accounts_ui_displays_only_profiles_and_remains_visible_after_refresh(tmp_path):
    """Execute the actual accounts JS inside a real browser, with a local mock GET."""
    from pathlib import Path
    source = Path("webui/static/accounts_view.js").read_text(encoding="utf-8")
    data = [{
        "id": "profile-1", "displayName": "Conta Mãe #1",
        "profileScope": "mae", "profileIndex": 1, "profileMode": "desktop",
        "status": "local", "stage": "form-not-found",
        "createdAt": "2026-10-15T00:00:00Z",
        "profileData": {"username": "marinacosta123", "isTestData": True},
        "registrationLog": [{
            "at": "2026-10-15T00:01:00Z",
            "stage": "form-not-found", "durationMs": 11000
        }]
    }]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for _ in range(2):  # simulate browser refresh and re-render
                page = browser.new_page()
                page.set_content("<!doctype html><html><body><div id='app'></div></body></html>")
                page.add_script_tag(content=source)
                page.evaluate(
                    """(accounts) => {
                      window.__directory = window.NexoraAccountsView.create({
                        get: async () => ({accounts}),
                        post: async () => { throw Error("No POST expected"); }
                      });
                      document.querySelector("#app").append(window.__directory.node);
                      window.__directory.onShow();
                    }""", data
                )
                page.locator(".accounts-card").first.wait_for(timeout=5000)
                assert page.locator(".accounts-card").count() == 1
                assert page.get_by_text("form-not-found").count() == 0  # user friendly name
                assert page.get_by_text("Formulário não encontrado").count() >= 1
                assert page.locator(".accounts-real-form").count() == 0
                assert page.get_by_text("Configurar site").count() == 0
                assert page.get_by_text("Adicionar conta").count() == 0
                page.close()
        finally:
            browser.close()
