"""Regression tests for URL normalization and HTTPS navigation.

Never opens a remote site or writes real browser profiles.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import importlib
import threading

import pytest

from Settings.url_normalizer import normalize_url, normalize_url_list

MODULES = (
    "Settings.Criar_Mae.gui_tab",
    "Settings.Criar_Filha.gui_tab",
)


@pytest.mark.parametrize(("source", "expected"), [
    ("exemplo.com", "https://exemplo.com"),
    (" www.exemplo.com ", "https://www.exemplo.com"),
    (" https://exemplo.com ", "https://exemplo.com"),
    ("http://exemplo.com", "http://exemplo.com"),
    ("HTTP://exemplo.com", "HTTP://exemplo.com"),
    ("exemplo.com/cadastro?id=1", "https://exemplo.com/cadastro?id=1"),
    (" localhost:8767 ", "https://localhost:8767"),
    ("127.0.0.1:8000", "https://127.0.0.1:8000"),
])
def test_url_https_default_and_explicit_scheme(source, expected):
    assert normalize_url(source) == expected
    assert normalize_url(expected) == expected


@pytest.mark.parametrize("source", [
    "", "   ", "dominio", "sem espaço.com", "ftp://exemplo.com",
    "javascript:alert(1)", "http:exemplo.com", "https://",
    "https://usuario:segredo@exemplo.com", "exemplo..com",
    "exemplo.com:abc", "exemplo.com:99999", "https://exemplo.com outro",
    "https://-exemplo.com", "exemplo.com,",
])
def test_url_rejects_invalid_addresses(source):
    if source == "exemplo.com,":
        with pytest.raises(ValueError):
            normalize_url_list(source)
    else:
        with pytest.raises(ValueError):
            normalize_url(source)


def test_comma_separated_urls_are_normalized_separately():
    assert normalize_url_list(" exemplo.com , www.exemplo.com, http://legado.test/a ") == [
        "https://exemplo.com", "https://www.exemplo.com", "http://legado.test/a"
    ]


@pytest.mark.parametrize("module_name", MODULES)
def test_run_all_passes_normalized_urls_to_each_profile(module_name, monkeypatch):
    module = importlib.import_module(module_name)
    captured = []

    def fake_instance(*args):
        captured.append(args[1])

    monkeypatch.setattr(module, "discover_extensions", lambda _base: [])
    monkeypatch.setattr(module, "run_instance", fake_instance)
    module.run_all(
        urls_string=" exemplo.com, http://legado.test ",
        mode="desktop",
        count_per_url=1,
        headless=True,
        proxy=None,
        auto_goto=True,
        keep_profiles=True,
        run_auto=False,
        start_game_enabled=False,
        start_game_name="",
        start_game_id="",
        stop_event=threading.Event(),
        winpos_controller=None,
        speed_controller=None,
        verify_phone_controller=None,
        mirror=None,
        withdraw_controller=None,
        game_launch_controller=None,
        instance_target_controller=None,
        log=lambda _message: None,
        delay_seconds=0,
    )
    assert captured == ["https://exemplo.com", "http://legado.test"]


@pytest.mark.parametrize("module_name", MODULES)
def test_https_failure_stops_browser_retry_loop(module_name, monkeypatch):
    module = importlib.import_module(module_name)
    events = []
    attempts = []
    closed = []

    class FakePage:
        url = ""

        def evaluate(self, *_args, **_kwargs):
            return None

        def on(self, *_args):
            pass

        def goto(self, url, **_kwargs):
            attempts.append(url)
            raise RuntimeError("net::ERR_SSL_PROTOCOL_ERROR")

    class FakeContext:
        def __init__(self):
            self.pages = [FakePage()]

        def add_init_script(self, *_args):
            pass

        def close(self):
            closed.append(True)

    fake_context = FakeContext()

    @contextmanager
    def fake_playwright():
        chromium = SimpleNamespace(
            launch_persistent_context=lambda **_kwargs: fake_context,
        )
        yield SimpleNamespace(chromium=chromium)

    monkeypatch.setattr(module, "sync_playwright", fake_playwright)
    monkeypatch.setattr(module, "ensure_clean_profile_dir", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "load_settings", lambda: {})
    monkeypatch.setattr(module, "fetch_public_ip_via_context", lambda _ctx: "")
    monkeypatch.setattr(module, "make_config", lambda *_args: SimpleNamespace(
        mode="desktop", viewport={"width": 1366, "height": 768},
        device_scale_factor=1.0, is_mobile=False, has_touch=False,
        user_agent="test-user-agent", webgl_vendor="Test", webgl_renderer="Test",
        extra_headers={},
    ))
    speed = SimpleNamespace(get=lambda: SimpleNamespace(enabled=False))
    mirror = SimpleNamespace(
        register=lambda _idx: None,
        install_receiver=lambda _page: None,
        unregister=lambda _idx: None,
    )
    module.run_instance(
        idx=1,
        url="https://exemplo.com",
        mode="desktop",
        headless=True,
        proxy=None,
        ext_paths=[],
        auto_goto=True,
        keep_profiles=True,
        run_auto=False,
        start_game_enabled=False,
        start_game_name="",
        start_game_id="",
        stop_event=threading.Event(),
        winpos_controller=None,
        speed_controller=speed,
        mirror=mirror,
        withdraw_controller=None,
        verify_phone_controller=None,
        game_launch_controller=None,
        instance_target_controller=None,
        instance_plan=None,
        log=events.append,
    )
    assert attempts == ["https://exemplo.com"]
    assert len(closed) == 1
    assert any("Não foi possível abrir" in entry and "HTTPS" in entry for entry in events)
    assert any("sem novas tentativas" in entry for entry in events)
