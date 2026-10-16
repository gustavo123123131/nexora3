"""Chromium persistence smoke test on localhost; never uses real websites."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import sync_playwright

from Settings.device_modes import stored_profile_directory
from nexora_accounts.local_profiles import LocalProfileStore


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b"<html><body>profile storage test</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


def test_saved_cookies_and_local_storage_survive_reopen_and_isolate_accounts(tmp_path, monkeypatch):
    from Settings.Criar_Mae.settings import utils
    monkeypatch.setattr(utils, "PROFILES_DIR", tmp_path / "profiles")
    store = LocalProfileStore(tmp_path / "generated_profiles.json")
    one = store.create("mae", device_mode="android")
    two = store.create("mae", device_mode="iphone")
    runtimes = [
        {"mode": "android", "user_agent": "NexoraTestAndroid/1",
         "viewport": {"width": 412, "height": 915},
         "device_scale_factor": 2.75, "is_mobile": True, "has_touch": True,
         "webgl_vendor": "Test GPU", "webgl_renderer": "Test Renderer", "extra_headers": {}},
        {"mode": "iphone", "user_agent": "NexoraTestIphone/1",
         "viewport": {"width": 390, "height": 844},
         "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
         "webgl_vendor": "Test GPU", "webgl_renderer": "Test Renderer", "extra_headers": {}},
    ]
    for profile, conf in zip((one, two), runtimes):
        store.set_runtime_config(profile["id"], conf)

    server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        with sync_playwright() as p:
            def open_saved(profile_id):
                record = LocalProfileStore(store.path).get(profile_id)
                conf = record["runtimeConfig"]
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(stored_profile_directory(
                        tmp_path / "profiles", record
                    )),
                    headless=True, user_agent=conf["user_agent"],
                    viewport=conf["viewport"],
                    device_scale_factor=conf["device_scale_factor"],
                    is_mobile=conf["is_mobile"], has_touch=conf["has_touch"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url)
                return context, page

            context_a, page_a = open_saved(one["id"])
            page_a.evaluate("localStorage.setItem('owner', 'Conta 1')")
            from time import time
            # Session-only cookies may expire on browser shutdown by design.
            # Persisted cookies with an explicit expiry must survive restarts.
            context_a.add_cookies([{
                "name": "accountCookie", "value": "from-account-1",
                "url": url, "expires": time() + 3600,
            }])
            assert "Android" in page_a.evaluate("navigator.userAgent")
            context_a.close()

            context_b, page_b = open_saved(two["id"])
            assert page_b.evaluate("localStorage.getItem('owner')") is None
            assert not any(c["name"] == "accountCookie" for c in context_b.cookies(url))
            page_b.evaluate("localStorage.setItem('owner', 'Conta 2')")
            context_b.close()

            again, page_again = open_saved(one["id"])
            assert page_again.evaluate("localStorage.getItem('owner')") == "Conta 1"
            assert any(c["name"] == "accountCookie" and c["value"] == "from-account-1"
                       for c in again.cookies(url))
            again.close()

        restored = LocalProfileStore(store.path).get(one["id"])
        assert restored["accountId"] == one["accountId"]
        assert restored["deviceProfileId"] == one["deviceProfileId"]
        assert restored["runtimeConfig"] == runtimes[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
